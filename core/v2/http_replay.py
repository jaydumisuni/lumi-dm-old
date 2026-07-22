"""Wave 2 HTTP replay layer.

This layer extends the proven Wave 1 transfer engine with encrypted request-header
replay, proxy support and POST-generated downloads without duplicating queue,
segment-journal or crash-recovery logic.
"""
from __future__ import annotations

from pathlib import Path
import os
import time
from typing import Any

import requests

from .http_transfer import (
    HTTPTransferRunner as Wave1HTTPTransferRunner,
    ProbeResult,
    RangeValidationError,
    RemoteChangedError,
    TransferCancelled,
    TransferPaused,
    _CHUNK_SIZE,
    _CONNECT_TIMEOUT,
    _MIN_SEGMENT,
    _READ_TIMEOUT,
    _base_headers,
    _filename_from_headers,
    _parse_content_range,
    validate_resume_identity,
)
from .models import DownloadTask, RequestEnvelope, TaskStatus, utc_now
from .vault import hydrate_post_body


def _proxy_map(envelope: RequestEnvelope) -> dict[str, str] | None:
    if not envelope.proxy_url:
        return None
    return {"http": envelope.proxy_url, "https": envelope.proxy_url}


def _request(
    session: requests.Session,
    envelope: RequestEnvelope,
    *,
    headers: dict[str, str],
    force_get: bool = False,
) -> requests.Response:
    method = "GET" if force_get else (envelope.method or "GET").upper()
    body: Any = None
    if method not in {"GET", "HEAD"} and envelope.post_body_reference:
        body = hydrate_post_body(envelope.post_body_reference)
    return session.request(
        method,
        envelope.final_url or envelope.url,
        headers=headers,
        data=body,
        proxies=_proxy_map(envelope),
        stream=True,
        allow_redirects=True,
        timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
    )


def probe_resource(
    envelope: RequestEnvelope,
    *,
    session: requests.Session | None = None,
) -> ProbeResult:
    """Probe replayable GET requests while preserving secrets and proxy policy."""
    if (envelope.method or "GET").upper() != "GET":
        raise ValueError("Non-GET resources are resolved from their transfer response")
    own_session = session is None
    session = session or requests.Session()
    session.trust_env = False
    headers = _base_headers(envelope)
    headers["Range"] = "bytes=0-0"
    response: requests.Response | None = None
    try:
        response = _request(session, envelope, headers=headers, force_get=True)
        if response.status_code in {401, 403, 410}:
            raise RemoteChangedError(
                f"Source requires a refreshed browser request ({response.status_code})"
            )
        if response.status_code == 206:
            parsed = _parse_content_range(response.headers.get("Content-Range", ""))
            if parsed is None or parsed[:2] != (0, 0):
                raise RangeValidationError(
                    "Invalid probe Content-Range: "
                    f"{response.headers.get('Content-Range', '')}"
                )
            total = parsed[2]
            range_supported = True
        elif response.status_code == 200:
            total = int(response.headers.get("Content-Length") or 0)
            range_supported = False
        else:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            range_supported = False
        return ProbeResult(
            final_url=str(response.url),
            total_bytes=total,
            range_supported=range_supported,
            etag=str(response.headers.get("ETag") or ""),
            last_modified=str(response.headers.get("Last-Modified") or ""),
            content_type=str(response.headers.get("Content-Type") or ""),
            filename=_filename_from_headers(response),
        )
    finally:
        if response is not None:
            response.close()
        if own_session:
            session.close()


class HTTPTransferRunner(Wave1HTTPTransferRunner):
    """Wave 1 runner plus secure browser-request replay."""

    def run(self) -> None:
        task = self._require_task()
        method = (task.request.method or "GET").upper()
        if method != "GET":
            self._run_non_get_task(task)
            return

        session = requests.Session()
        session.trust_env = False
        try:
            task.status = TaskStatus.RESOLVING.value
            task.started_at = task.started_at or utc_now()
            self._save(task, "resolving")
            probe = probe_resource(task.request, session=session)
            validate_resume_identity(task, probe)
            task.request.final_url = probe.final_url
            task.total_bytes = probe.total_bytes
            task.range_supported = probe.range_supported
            task.etag = probe.etag or task.etag
            task.last_modified = probe.last_modified or task.last_modified
            task.content_type = probe.content_type
            if probe.filename and task.filename in {"", "download.bin"}:
                task.filename = probe.filename
                task.final_path = str(Path(task.target_dir) / task.filename)
                task.partial_path = str(Path(task.temp_dir) / f"{task.filename}.part")
            self._ensure_disk_space(task)
            self._save(task, "resolved")
            if (
                task.range_supported
                and task.total_bytes >= _MIN_SEGMENT
                and task.connections > 1
            ):
                self._run_parallel(task)
            else:
                self._run_single(task, session)
        except TransferPaused:
            self._finish_control(TaskStatus.PAUSED.value, "paused")
        except TransferCancelled:
            self._finish_control(TaskStatus.CANCELLED.value, "cancelled")
        except RemoteChangedError as exc:
            current = self._require_task()
            current.status = TaskStatus.NEEDS_LINK.value
            current.error = str(exc)
            current.error_code = "remote_changed"
            current.speed_bytes_per_sec = 0
            self._save(current, "needs_link", {"error": str(exc)})
        except Exception as exc:
            current = self._require_task()
            current.status = TaskStatus.FAILED.value
            current.finished_at = utc_now()
            current.error = str(exc)
            current.error_code = self._error_code(exc)
            current.speed_bytes_per_sec = 0
            self._save(current, "failed", {"error": str(exc)})
        finally:
            session.close()

    def _run_non_get_task(self, task: DownloadTask) -> None:
        session = requests.Session()
        session.trust_env = False
        response: requests.Response | None = None
        try:
            partial = Path(task.partial_path)
            final = Path(task.final_path)
            partial.parent.mkdir(parents=True, exist_ok=True)
            final.parent.mkdir(parents=True, exist_ok=True)
            if partial.exists() and partial.stat().st_size:
                raise RemoteChangedError(
                    "POST-generated downloads require Repair Download Link before retry"
                )

            task.status = TaskStatus.RESOLVING.value
            task.started_at = task.started_at or utc_now()
            self._save(task, "resolving")
            response = _request(
                session,
                task.request,
                headers=_base_headers(task.request),
            )
            if response.status_code in {401, 403, 410}:
                raise RemoteChangedError(
                    f"Source requires a refreshed browser request ({response.status_code})"
                )
            response.raise_for_status()
            task.request.final_url = str(response.url)
            task.total_bytes = int(response.headers.get("Content-Length") or 0)
            task.etag = str(response.headers.get("ETag") or "")
            task.last_modified = str(response.headers.get("Last-Modified") or "")
            task.content_type = str(response.headers.get("Content-Type") or "")
            response_name = _filename_from_headers(response)
            if response_name and task.filename in {"", "download.bin"}:
                task.filename = response_name
                task.final_path = str(Path(task.target_dir) / response_name)
                task.partial_path = str(Path(task.temp_dir) / f"{response_name}.part")
                partial = Path(task.partial_path)
                final = Path(task.final_path)
                partial.parent.mkdir(parents=True, exist_ok=True)
                final.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_disk_space(task)
            task.status = TaskStatus.RUNNING.value
            task.mode = "request-replay"
            self._save(task, "transfer_started")
            started = time.monotonic()
            with partial.open("wb") as handle:
                for chunk in response.iter_content(_CHUNK_SIZE):
                    self._check_control()
                    if not chunk:
                        continue
                    handle.write(chunk)
                    task.downloaded_bytes += len(chunk)
                    self._throttle(task, len(chunk))
                    self._report(task, started)
            self._complete_file(task, partial, final)
        except TransferPaused:
            partial = Path(task.partial_path)
            partial.unlink(missing_ok=True)
            self._finish_control(TaskStatus.NEEDS_LINK.value, "needs_link")
        except TransferCancelled:
            Path(task.partial_path).unlink(missing_ok=True)
            self._finish_control(TaskStatus.CANCELLED.value, "cancelled")
        except RemoteChangedError as exc:
            current = self._require_task()
            current.status = TaskStatus.NEEDS_LINK.value
            current.error = str(exc)
            current.error_code = "request_refresh_required"
            self._save(current, "needs_link", {"error": str(exc)})
        except Exception as exc:
            current = self._require_task()
            current.status = TaskStatus.FAILED.value
            current.finished_at = utc_now()
            current.error = str(exc)
            current.error_code = self._error_code(exc)
            self._save(current, "failed", {"error": str(exc)})
        finally:
            if response is not None:
                response.close()
            session.close()

    def _run_single(self, task: DownloadTask, session: requests.Session) -> None:
        partial = Path(task.partial_path)
        final = Path(task.final_path)
        partial.parent.mkdir(parents=True, exist_ok=True)
        final.parent.mkdir(parents=True, exist_ok=True)
        existing = partial.stat().st_size if partial.exists() else 0
        headers = _base_headers(task.request)
        if existing:
            headers["Range"] = f"bytes={existing}-"
        response = _request(session, task.request, headers=headers, force_get=True)
        if response.status_code in {401, 403, 410}:
            response.close()
            raise RemoteChangedError(
                f"Source requires a refreshed browser request ({response.status_code})"
            )
        if existing:
            if response.status_code != 206:
                response.close()
                raise RangeValidationError("Server ignored the resume Range request")
            parsed = _parse_content_range(response.headers.get("Content-Range", ""))
            if parsed is None or parsed[0] != existing:
                response.close()
                raise RangeValidationError(
                    "Resume started at the wrong byte: "
                    f"{response.headers.get('Content-Range', '')}"
                )
        else:
            response.raise_for_status()

        task.status = TaskStatus.RUNNING.value
        task.mode = "single"
        task.downloaded_bytes = existing
        self._save(task, "transfer_started")
        started = time.monotonic()
        with response, partial.open("ab" if existing else "wb") as handle:
            for chunk in response.iter_content(_CHUNK_SIZE):
                self._check_control()
                if not chunk:
                    continue
                handle.write(chunk)
                task.downloaded_bytes += len(chunk)
                self._throttle(task, len(chunk))
                self._report(task, started)
        self._complete_file(task, partial, final)

    def _download_segment(
        self,
        task: DownloadTask,
        segment,
        session: requests.Session,
        coordinator,
        started: float,
    ) -> None:
        current = segment.next_byte
        if current > segment.end:
            return
        headers = _base_headers(task.request)
        headers["Range"] = f"bytes={current}-{segment.end}"
        response = _request(session, task.request, headers=headers, force_get=True)
        if response.status_code in {401, 403, 410}:
            response.close()
            raise RemoteChangedError(
                f"Source requires a refreshed browser request ({response.status_code})"
            )
        if response.status_code != 206:
            response.close()
            raise RangeValidationError(
                f"Expected 206 for bytes {current}-{segment.end}, "
                f"got {response.status_code}"
            )
        parsed = _parse_content_range(response.headers.get("Content-Range", ""))
        if (
            parsed is None
            or parsed[0] != current
            or parsed[1] > segment.end
            or parsed[2] != task.total_bytes
        ):
            response.close()
            raise RangeValidationError(
                f"Unexpected Content-Range: {response.headers.get('Content-Range', '')}"
            )
        with response, Path(task.partial_path).open("r+b") as handle:
            handle.seek(current)
            for chunk in response.iter_content(_CHUNK_SIZE):
                self._check_control()
                if not chunk:
                    continue
                allowed = min(len(chunk), segment.end - current + 1)
                if allowed <= 0:
                    break
                handle.write(chunk[:allowed])
                current += allowed
                coordinator.progress(segment, allowed)
                task.downloaded_bytes = coordinator.total_downloaded()
                self._throttle(task, allowed)
                self._report(task, started)
                self._write_journal(task, coordinator)
                if current > segment.end:
                    break
        if current - 1 != segment.end:
            raise IOError(f"Segment ended at {current - 1}, expected {segment.end}")

    def _finish_control(self, status: str, event: str) -> None:
        task = self._require_task()
        task.status = status
        task.speed_bytes_per_sec = 0
        if status in {TaskStatus.CANCELLED.value, TaskStatus.NEEDS_LINK.value}:
            task.finished_at = utc_now()
        self._save(task, event)
