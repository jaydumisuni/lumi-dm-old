"""Reliable adaptive HTTP transfer engine for Lumi DM v2."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Callable, Any

import requests
from requests.adapters import HTTPAdapter

from .models import DownloadTask, RequestEnvelope, SegmentState, TaskStatus, utc_now
from .store import StateStore


_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.I)
_CHUNK_SIZE = 1024 * 1024
_MIN_SEGMENT = 2 * 1024 * 1024
_CONNECT_TIMEOUT = 20
_READ_TIMEOUT = 90


class TransferPaused(Exception):
    pass


class TransferCancelled(Exception):
    pass


class RemoteChangedError(RuntimeError):
    pass


class RangeValidationError(RuntimeError):
    pass


@dataclass(slots=True)
class ProbeResult:
    final_url: str
    total_bytes: int
    range_supported: bool
    etag: str
    last_modified: str
    content_type: str
    filename: str


def _filename_from_headers(response: requests.Response) -> str:
    disposition = response.headers.get("Content-Disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
    if match:
        from urllib.parse import unquote
        return Path(unquote(match.group(1))).name
    match = re.search(r'filename="?([^";]+)"?', disposition, re.I)
    if match:
        return Path(match.group(1).strip()).name
    from urllib.parse import urlparse, unquote
    candidate = Path(unquote(urlparse(str(response.url)).path)).name
    return candidate or "download.bin"


def _base_headers(envelope: RequestEnvelope) -> dict[str, str]:
    headers = envelope.normalized_headers()
    headers.setdefault("User-Agent", "Lumi-DM/2.0")
    return headers


def _parse_content_range(value: str) -> tuple[int, int, int] | None:
    match = _CONTENT_RANGE_RE.match((value or "").strip())
    if not match or match.group(3) == "*":
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def probe_resource(
    envelope: RequestEnvelope,
    *,
    session: requests.Session | None = None,
) -> ProbeResult:
    """Probe with a one-byte range request so range support is proven, not guessed."""
    own_session = session is None
    session = session or requests.Session()
    session.trust_env = False
    headers = _base_headers(envelope)
    headers["Range"] = "bytes=0-0"
    response = None
    try:
        response = session.get(
            envelope.final_url or envelope.url,
            headers=headers,
            stream=True,
            allow_redirects=True,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
        )
        if response.status_code == 206:
            parsed = _parse_content_range(response.headers.get("Content-Range", ""))
            if not parsed or parsed[0] != 0 or parsed[1] != 0:
                raise RangeValidationError(
                    f"Invalid probe Content-Range: {response.headers.get('Content-Range', '')}"
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


def validate_resume_identity(task: DownloadTask, probe: ProbeResult) -> None:
    if task.total_bytes and probe.total_bytes and task.total_bytes != probe.total_bytes:
        raise RemoteChangedError(
            f"Remote size changed from {task.total_bytes} to {probe.total_bytes}"
        )
    if task.etag and probe.etag and task.etag != probe.etag:
        raise RemoteChangedError("Remote ETag changed")
    if (
        not task.etag
        and task.last_modified
        and probe.last_modified
        and task.last_modified != probe.last_modified
    ):
        raise RemoteChangedError("Remote Last-Modified changed")


class SegmentCoordinator:
    def __init__(
        self,
        segments: list[SegmentState],
        *,
        minimum_segment: int = _MIN_SEGMENT,
    ):
        self.segments = segments
        self.minimum_segment = max(256 * 1024, int(minimum_segment))
        self._lock = threading.RLock()

    def ensure_claimable(self, target_count: int) -> None:
        """Split the largest pending segment until enough work exists."""
        with self._lock:
            while self._claimable_count() < target_count:
                candidates = [
                    segment
                    for segment in self.segments
                    if segment.status == "pending"
                    and segment.remaining >= self.minimum_segment * 2
                ]
                if not candidates:
                    break
                largest = max(candidates, key=lambda segment: segment.remaining)
                split_at = largest.next_byte + largest.remaining // 2
                right = SegmentState(start=split_at, end=largest.end)
                largest.end = split_at - 1
                self.segments.append(right)

    def _claimable_count(self) -> int:
        return sum(
            1
            for segment in self.segments
            if segment.status == "pending" and segment.remaining > 0
        )

    def claim(self, worker_id: str) -> SegmentState | None:
        with self._lock:
            candidates = [
                segment
                for segment in self.segments
                if segment.status == "pending" and segment.remaining > 0
            ]
            if not candidates:
                return None
            segment = max(candidates, key=lambda item: item.remaining)
            segment.status = "active"
            segment.worker_id = worker_id
            segment.attempts += 1
            return segment

    def progress(self, segment: SegmentState, amount: int) -> None:
        with self._lock:
            segment.downloaded = min(segment.length, segment.downloaded + amount)

    def done(self, segment: SegmentState) -> None:
        with self._lock:
            segment.downloaded = segment.length
            segment.status = "done"
            segment.worker_id = ""
            segment.last_error = ""

    def fail(self, segment: SegmentState, error: str, *, retry: bool) -> None:
        with self._lock:
            segment.status = "pending" if retry else "failed"
            segment.worker_id = ""
            segment.last_error = error

    def all_done(self) -> bool:
        with self._lock:
            return bool(self.segments) and all(
                segment.remaining == 0 and segment.status == "done"
                for segment in self.segments
            )

    def any_failed(self) -> bool:
        with self._lock:
            return any(segment.status == "failed" for segment in self.segments)

    def total_downloaded(self) -> int:
        with self._lock:
            return sum(segment.downloaded for segment in self.segments)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(segment) for segment in self.segments]


class HTTPTransferRunner:
    def __init__(
        self,
        store: StateStore,
        task_id: str,
        *,
        pause_event: threading.Event,
        cancel_event: threading.Event,
        update_callback: Callable[[DownloadTask], None],
    ):
        self.store = store
        self.task_id = task_id
        self.pause_event = pause_event
        self.cancel_event = cancel_event
        self.update_callback = update_callback
        self._journal_lock = threading.Lock()
        self._last_journal = 0.0
        self._last_report = 0.0
        self._speed_points: list[tuple[float, int]] = []

    def run(self) -> None:
        task = self._require_task()
        session = requests.Session()
        session.trust_env = False
        adapter = HTTPAdapter(
            pool_connections=max(2, task.connections),
            pool_maxsize=max(4, task.connections + 2),
            max_retries=0,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
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

            if task.range_supported and task.total_bytes >= _MIN_SEGMENT and task.connections > 1:
                self._run_parallel(task)
            else:
                self._run_single(task, session)
        except TransferPaused:
            task = self._require_task()
            task.status = TaskStatus.PAUSED.value
            task.speed_bytes_per_sec = 0
            self._save(task, "paused")
        except TransferCancelled:
            task = self._require_task()
            task.status = TaskStatus.CANCELLED.value
            task.finished_at = utc_now()
            task.speed_bytes_per_sec = 0
            self._save(task, "cancelled")
        except RemoteChangedError as exc:
            task = self._require_task()
            task.status = TaskStatus.NEEDS_LINK.value
            task.error = str(exc)
            task.error_code = "remote_changed"
            task.speed_bytes_per_sec = 0
            self._save(task, "needs_link", {"error": str(exc)})
        except Exception as exc:
            task = self._require_task()
            task.status = TaskStatus.FAILED.value
            task.finished_at = utc_now()
            task.error = str(exc)
            task.error_code = self._error_code(exc)
            task.speed_bytes_per_sec = 0
            self._save(task, "failed", {"error": str(exc)})
        finally:
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
        response = session.get(
            task.request.final_url or task.request.url,
            headers=headers,
            stream=True,
            allow_redirects=True,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
        )
        if existing:
            if response.status_code != 206:
                response.close()
                raise RangeValidationError("Server ignored the resume Range request")
            parsed = _parse_content_range(response.headers.get("Content-Range", ""))
            if not parsed or parsed[0] != existing:
                response.close()
                raise RangeValidationError(
                    f"Resume started at the wrong byte: {response.headers.get('Content-Range', '')}"
                )
        else:
            response.raise_for_status()

        task.status = TaskStatus.RUNNING.value
        task.mode = "single"
        task.downloaded_bytes = existing
        self._save(task, "transfer_started")
        started = time.monotonic()
        mode = "ab" if existing else "wb"
        with response, partial.open(mode) as handle:
            for chunk in response.iter_content(_CHUNK_SIZE):
                self._check_control()
                if not chunk:
                    continue
                handle.write(chunk)
                task.downloaded_bytes += len(chunk)
                self._report(task, started)
        self._complete_file(task, partial, final)

    def _run_parallel(self, task: DownloadTask) -> None:
        partial = Path(task.partial_path)
        final = Path(task.final_path)
        partial.parent.mkdir(parents=True, exist_ok=True)
        final.parent.mkdir(parents=True, exist_ok=True)
        if not partial.exists() or partial.stat().st_size != task.total_bytes:
            with partial.open("wb") as handle:
                handle.truncate(task.total_bytes)

        journal = self.store.load_resume(task.id)
        if journal and self._journal_matches(task, journal):
            segments = [
                SegmentState.from_dict(item)
                for item in list(journal.get("segments") or [])
            ]
            for segment in segments:
                if segment.status in {"active", "failed"}:
                    segment.status = "pending"
                    segment.worker_id = ""
        else:
            segments = [SegmentState(0, task.total_bytes - 1)]
        coordinator = SegmentCoordinator(segments)
        task.status = TaskStatus.RUNNING.value
        task.mode = "adaptive"
        task.downloaded_bytes = coordinator.total_downloaded()
        self._save(task, "transfer_started")
        self._write_journal(task, coordinator, force=True)

        error_lock = threading.Lock()
        fatal_errors: list[Exception] = []
        success_signal = threading.Event()
        active_threads: list[threading.Thread] = []
        started = time.monotonic()
        target_workers = 1

        def worker(index: int) -> None:
            worker_id = f"http-{index}"
            worker_session = requests.Session()
            worker_session.trust_env = False
            adapter = HTTPAdapter(pool_connections=1, pool_maxsize=2, max_retries=0)
            worker_session.mount("http://", adapter)
            worker_session.mount("https://", adapter)
            try:
                while True:
                    self._check_control()
                    segment = coordinator.claim(worker_id)
                    if segment is None:
                        return
                    try:
                        self._download_segment(task, segment, worker_session, coordinator, started)
                        coordinator.done(segment)
                        self._write_journal(task, coordinator, force=True)
                        success_signal.set()
                    except (TransferPaused, TransferCancelled):
                        coordinator.fail(segment, "interrupted", retry=True)
                        raise
                    except Exception as exc:
                        retry = segment.attempts < 3
                        coordinator.fail(segment, str(exc), retry=retry)
                        self._write_journal(task, coordinator, force=True)
                        if not retry:
                            with error_lock:
                                fatal_errors.append(exc)
                            return
                        time.sleep(min(4, 2 ** max(0, segment.attempts - 1)))
            finally:
                worker_session.close()

        while True:
            self._check_control()
            coordinator.ensure_claimable(max(4, target_workers))
            while len(active_threads) < target_workers:
                thread = threading.Thread(
                    target=worker,
                    args=(len(active_threads),),
                    name=f"lumi-http-{task.id[:8]}-{len(active_threads)}",
                    daemon=True,
                )
                active_threads.append(thread)
                thread.start()

            if coordinator.all_done():
                break
            if fatal_errors or coordinator.any_failed():
                break

            success_signal.wait(timeout=0.5)
            if success_signal.is_set() and target_workers < task.connections:
                success_signal.clear()
                target_workers = min(task.connections, max(2, target_workers * 2))
            self._report_from_segments(task, coordinator, started)

            if active_threads and all(not thread.is_alive() for thread in active_threads):
                if coordinator.all_done():
                    break
                active_threads = []
                target_workers = min(task.connections, max(1, target_workers))

        for thread in active_threads:
            thread.join(timeout=5)
        self._check_control()
        if fatal_errors:
            raise fatal_errors[0]
        if not coordinator.all_done():
            raise RuntimeError("Segment transfer ended before all ranges completed")
        task.downloaded_bytes = task.total_bytes
        self._write_journal(task, coordinator, force=True)
        self._complete_file(task, partial, final)

    def _download_segment(
        self,
        task: DownloadTask,
        segment: SegmentState,
        session: requests.Session,
        coordinator: SegmentCoordinator,
        started: float,
    ) -> None:
        current = segment.next_byte
        if current > segment.end:
            return
        headers = _base_headers(task.request)
        headers["Range"] = f"bytes={current}-{segment.end}"
        response = session.get(
            task.request.final_url or task.request.url,
            headers=headers,
            stream=True,
            allow_redirects=True,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
        )
        if response.status_code in {401, 403, 410}:
            response.close()
            raise RemoteChangedError(
                f"Source requires a refreshed browser request ({response.status_code})"
            )
        if response.status_code != 206:
            response.close()
            raise RangeValidationError(
                f"Expected 206 for bytes {current}-{segment.end}, got {response.status_code}"
            )
        parsed = _parse_content_range(response.headers.get("Content-Range", ""))
        if (
            not parsed
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
                self._report(task, started)
                self._write_journal(task, coordinator)
                if current > segment.end:
                    break
        if current - 1 != segment.end:
            raise IOError(f"Segment ended at {current - 1}, expected {segment.end}")

    def _report_from_segments(
        self,
        task: DownloadTask,
        coordinator: SegmentCoordinator,
        started: float,
    ) -> None:
        task.downloaded_bytes = coordinator.total_downloaded()
        self._report(task, started)

    def _report(self, task: DownloadTask, started: float) -> None:
        now = time.monotonic()
        if now - self._last_report < 0.25:
            return
        self._last_report = now
        self._speed_points.append((now, task.downloaded_bytes))
        cutoff = now - 5.0
        self._speed_points = [point for point in self._speed_points if point[0] >= cutoff]
        if len(self._speed_points) >= 2:
            elapsed = max(0.001, self._speed_points[-1][0] - self._speed_points[0][0])
            moved = self._speed_points[-1][1] - self._speed_points[0][1]
            task.speed_bytes_per_sec = max(0.0, moved / elapsed)
        else:
            task.speed_bytes_per_sec = task.downloaded_bytes / max(0.001, now - started)
        task.progress_percent = (
            round(task.downloaded_bytes * 100 / task.total_bytes, 2)
            if task.total_bytes
            else 0.0
        )
        self.store.save_task(task)
        self.update_callback(task)

    def _complete_file(self, task: DownloadTask, partial: Path, final: Path) -> None:
        if task.total_bytes and partial.stat().st_size != task.total_bytes:
            raise IOError(
                f"Final size mismatch: expected {task.total_bytes}, got {partial.stat().st_size}"
            )
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, final)
        self.store.delete_resume(task.id)
        task.status = TaskStatus.COMPLETED.value
        task.finished_at = utc_now()
        task.downloaded_bytes = final.stat().st_size
        task.total_bytes = max(task.total_bytes, task.downloaded_bytes)
        task.progress_percent = 100.0
        task.speed_bytes_per_sec = 0.0
        task.error = ""
        task.error_code = ""
        self._save(task, "completed")

    def _write_journal(
        self,
        task: DownloadTask,
        coordinator: SegmentCoordinator,
        *,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force and now - self._last_journal < 0.75:
            return
        with self._journal_lock:
            if not force and now - self._last_journal < 0.75:
                return
            self._last_journal = now
            self.store.save_resume(
                task.id,
                {
                    "schema_version": 2,
                    "task_id": task.id,
                    "url": task.request.url,
                    "final_url": task.request.final_url,
                    "total_bytes": task.total_bytes,
                    "etag": task.etag,
                    "last_modified": task.last_modified,
                    "segments": coordinator.snapshot(),
                    "saved_at": utc_now(),
                },
            )

    @staticmethod
    def _journal_matches(task: DownloadTask, journal: dict[str, Any]) -> bool:
        return (
            int(journal.get("schema_version") or 0) == 2
            and int(journal.get("total_bytes") or 0) == task.total_bytes
            and (not task.etag or journal.get("etag") == task.etag)
            and (
                not task.last_modified
                or journal.get("last_modified") == task.last_modified
            )
        )

    def _check_control(self) -> None:
        if self.cancel_event.is_set():
            raise TransferCancelled()
        if self.pause_event.is_set():
            raise TransferPaused()

    def _ensure_disk_space(self, task: DownloadTask) -> None:
        if not task.total_bytes:
            return
        destination = Path(task.partial_path).parent
        destination.mkdir(parents=True, exist_ok=True)
        existing = Path(task.partial_path).stat().st_size if Path(task.partial_path).exists() else 0
        needed = max(0, task.total_bytes - existing)
        free = shutil.disk_usage(destination).free
        if free < needed:
            raise OSError(f"Not enough disk space: need {needed} bytes, have {free} bytes")

    def _require_task(self) -> DownloadTask:
        task = self.store.get_task(self.task_id)
        if task is None:
            raise KeyError(self.task_id)
        return task

    def _save(
        self,
        task: DownloadTask,
        event: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.store.save_task(task)
        self.store.append_event(task.id, event, payload)
        self.update_callback(task)

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, RangeValidationError):
            return "range_validation"
        if isinstance(exc, requests.Timeout):
            return "network_timeout"
        if isinstance(exc, requests.ConnectionError):
            return "network_connection"
        if isinstance(exc, OSError):
            return "filesystem"
        return "transfer_failed"
