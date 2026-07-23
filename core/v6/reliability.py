"""Final HTTP reliability and filename hardening for Lumi.

This layer intentionally patches the proven v2 transfer classes instead of
forking the queue, journal, vault or recovery systems.  It adds:

* RFC 5987/2231 filename handling and content-type extension recovery;
* retrying probes and resumable single-stream transfers;
* longer timeouts and conservative connection counts for fragile hosts;
* exact final-size verification before a task can become completed.
"""
from __future__ import annotations

from email.message import Message
import mimetypes
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import unquote, urlparse

import requests


_GENERIC_NAMES = {"", "download", "download.bin", "file", "file.bin", "unknown", "unknown.bin"}
_SLOW_HOST_SUFFIXES = (
    "samfw.com",
    "samfw.net",
    "androidfilehost.com",
    "needrom.com",
)
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}
_MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-7z-compressed": ".7z",
    "application/x-rar-compressed": ".rar",
    "application/vnd.android.package-archive": ".apk",
    "application/x-iso9660-image": ".iso",
    "application/octet-stream": "",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "video/mp4": ".mp4",
    "video/x-matroska": ".mkv",
    "audio/mpeg": ".mp3",
}
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{value}" for value in range(1, 10)),
    *(f"LPT{value}" for value in range(1, 10)),
}
_installed = False


def _content_type(value: str) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _extension_for(content_type: str) -> str:
    kind = _content_type(content_type)
    if kind in _MIME_EXTENSIONS:
        return _MIME_EXTENSIONS[kind]
    guessed = mimetypes.guess_extension(kind, strict=False) or ""
    return ".jpg" if guessed == ".jpe" else guessed


def _safe_filename(value: str) -> str:
    name = Path(str(value or "").replace("\\", "/")).name.strip().strip(". ")
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip().strip(". ")
    if not name:
        return "download"
    stem = Path(name).stem.upper()
    if stem in _WINDOWS_RESERVED:
        name = f"_{name}"
    return name[:240]


def _disposition_filename(value: str) -> str:
    if not value:
        return ""
    message = Message()
    message["Content-Disposition"] = value
    candidate = message.get_filename() or ""
    if isinstance(candidate, tuple):
        candidate = "".join(str(part or "") for part in candidate)
    candidate = unquote(str(candidate).strip().strip('"'))
    return _safe_filename(candidate) if candidate else ""


def _url_filename(url: str) -> str:
    try:
        return _safe_filename(unquote(Path(urlparse(str(url or "")).path).name))
    except Exception:
        return ""


def infer_filename(*, disposition: str = "", final_url: str = "", original_url: str = "", content_type: str = "") -> str:
    """Choose the strongest safe filename and recover a missing extension."""
    candidates = [
        _disposition_filename(disposition),
        _url_filename(final_url),
        _url_filename(original_url),
    ]
    name = next((item for item in candidates if item and item.lower() not in _GENERIC_NAMES), "")
    if not name:
        name = next((item for item in candidates if item), "download")
    name = _safe_filename(name)
    extension = _extension_for(content_type)
    suffix = Path(name).suffix.lower()
    if extension and not suffix:
        name += extension
    elif extension and name.lower() in {"download.bin", "file.bin", "unknown.bin"}:
        name = f"{Path(name).stem}{extension}"
    return _safe_filename(name)


def normalize_user_filename(filename: str, *, content_type: str = "", final_url: str = "", original_url: str = "") -> str:
    """Respect an edited name while adding an extension when evidence is strong."""
    cleaned = _safe_filename(filename)
    inferred = infer_filename(final_url=final_url, original_url=original_url, content_type=content_type)
    inferred_extension = Path(inferred).suffix
    if not Path(cleaned).suffix and inferred_extension:
        cleaned += inferred_extension
    if cleaned.lower() in _GENERIC_NAMES and inferred:
        cleaned = inferred
    return _safe_filename(cleaned)


def _is_slow_host(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _SLOW_HOST_SUFFIXES)


def _retry_delay(attempt: int, response: requests.Response | None = None) -> float:
    if response is not None:
        retry_after = str(response.headers.get("Retry-After") or "").strip()
        if retry_after.isdigit():
            return min(30.0, max(1.0, float(retry_after)))
    return min(12.0, 0.75 * (2 ** max(0, attempt - 1)))


def install_reliability() -> None:
    global _installed
    if _installed:
        return

    from core.v2 import http_replay, http_transfer, runtime
    from core.v2.models import TaskStatus

    _installed = True

    # Firmware and signed-file hosts frequently need longer response windows.
    http_transfer._CONNECT_TIMEOUT = 30
    http_transfer._READ_TIMEOUT = 180
    http_replay._CONNECT_TIMEOUT = 30
    http_replay._READ_TIMEOUT = 180

    original_base_filename = http_transfer._filename_from_headers

    def filename_from_headers(response: requests.Response) -> str:
        try:
            return infer_filename(
                disposition=str(response.headers.get("Content-Disposition") or ""),
                final_url=str(response.url or ""),
                content_type=str(response.headers.get("Content-Type") or ""),
            )
        except Exception:
            return original_base_filename(response)

    http_transfer._filename_from_headers = filename_from_headers
    http_replay._filename_from_headers = filename_from_headers

    original_probe = http_replay.probe_resource

    def reliable_probe(envelope, *, session=None):
        attempts = 6 if _is_slow_host(envelope.final_url or envelope.url) else 4
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return original_probe(envelope, session=session)
            except http_transfer.RemoteChangedError:
                raise
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                time.sleep(_retry_delay(attempt))
        raise last_error or RuntimeError("Lumi could not resolve the remote file")

    http_replay.probe_resource = reliable_probe
    runtime.probe_resource = reliable_probe

    runner_class = http_replay.HTTPTransferRunner
    original_run = runner_class.run
    original_complete = runner_class._complete_file

    def reliable_run(self) -> None:
        task = self._require_task()
        source = task.request.final_url or task.request.url
        if _is_slow_host(source):
            task.connections = 1
            task.metadata = dict(task.metadata or {})
            task.metadata["reliability_profile"] = "slow-host"
            task.metadata["reliability_note"] = "Conservative single-stream mode with progressive resume"
        return original_run(self)

    def complete_with_filename(self, task, partial: Path, final: Path) -> None:
        corrected = normalize_user_filename(
            task.filename,
            content_type=task.content_type,
            final_url=task.request.final_url,
            original_url=task.request.url,
        )
        if corrected != task.filename:
            task.filename = corrected
            final = Path(task.target_dir) / corrected
            task.final_path = str(final)
        return original_complete(self, task, partial, final)

    def reliable_single(self, task, session: requests.Session) -> None:
        partial = Path(task.partial_path)
        partial.parent.mkdir(parents=True, exist_ok=True)
        attempts = 6 if _is_slow_host(task.request.final_url or task.request.url) else 4
        started = time.monotonic()
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            self._check_control()
            partial = Path(task.partial_path)
            final = Path(task.final_path)
            partial.parent.mkdir(parents=True, exist_ok=True)
            final.parent.mkdir(parents=True, exist_ok=True)
            existing = partial.stat().st_size if partial.exists() else 0
            headers = http_replay._base_headers(task.request)
            if existing:
                headers["Range"] = f"bytes={existing}-"
            response: requests.Response | None = None
            try:
                response = http_replay._request(session, task.request, headers=headers, force_get=True)
                if response.status_code in {401, 403, 410}:
                    raise http_transfer.RemoteChangedError(
                        f"Source requires a refreshed browser request ({response.status_code})"
                    )
                if response.status_code in _TRANSIENT_STATUS:
                    delay = _retry_delay(attempt, response)
                    response.close()
                    if attempt >= attempts:
                        response.raise_for_status()
                    time.sleep(delay)
                    continue

                if existing and response.status_code == 200:
                    # A no-range host cannot resume safely. Restart only the partial
                    # file, never the final destination.
                    response.close()
                    partial.unlink(missing_ok=True)
                    task.downloaded_bytes = 0
                    task.range_supported = False
                    if attempt >= attempts:
                        raise http_transfer.RangeValidationError(
                            "Server ignored resume requests repeatedly"
                        )
                    time.sleep(_retry_delay(attempt))
                    continue
                if existing:
                    if response.status_code != 206:
                        raise http_transfer.RangeValidationError(
                            f"Expected 206 while resuming, got {response.status_code}"
                        )
                    parsed = http_replay._parse_content_range(
                        response.headers.get("Content-Range", "")
                    )
                    if parsed is None or parsed[0] != existing:
                        raise http_transfer.RangeValidationError(
                            "Resume response started at the wrong byte"
                        )
                else:
                    response.raise_for_status()

                task.status = TaskStatus.RUNNING.value
                task.mode = "single-reliable"
                task.downloaded_bytes = existing
                task.metadata = dict(task.metadata or {})
                task.metadata["transfer_attempt"] = attempt
                self._save(task, "transfer_started" if attempt == 1 else "transfer_resumed")

                response_name = filename_from_headers(response)
                if task.filename.lower() in _GENERIC_NAMES:
                    task.filename = response_name
                    task.final_path = str(Path(task.target_dir) / response_name)
                    task.partial_path = str(Path(task.temp_dir) / f"{response_name}.part")
                    if existing == 0 and Path(task.partial_path) != partial:
                        replacement = Path(task.partial_path)
                        replacement.parent.mkdir(parents=True, exist_ok=True)
                        if partial.exists():
                            os.replace(partial, replacement)
                        partial = replacement
                        final = Path(task.final_path)

                mode = "ab" if existing else "wb"
                with response, partial.open(mode) as handle:
                    for chunk in response.iter_content(http_replay._CHUNK_SIZE):
                        self._check_control()
                        if not chunk:
                            continue
                        handle.write(chunk)
                        task.downloaded_bytes += len(chunk)
                        self._throttle(task, len(chunk))
                        self._report(task, started)
                    handle.flush()
                    os.fsync(handle.fileno())

                actual = partial.stat().st_size
                if task.total_bytes and actual != task.total_bytes:
                    raise IOError(
                        f"Transfer ended early at {actual} of {task.total_bytes} bytes"
                    )
                self._complete_file(task, partial, final)
                return
            except (http_transfer.TransferPaused, http_transfer.TransferCancelled, http_transfer.RemoteChangedError):
                raise
            except Exception as exc:
                last_error = exc
                task.metadata = dict(task.metadata or {})
                task.metadata["last_retry_error"] = str(exc)[:500]
                task.metadata["retry_attempt"] = attempt
                self._save(task, "transfer_retry", {"attempt": attempt, "error": str(exc)[:500]})
                if attempt >= attempts:
                    raise
                time.sleep(_retry_delay(attempt))
            finally:
                if response is not None:
                    response.close()

        raise last_error or RuntimeError("Download did not complete")

    runner_class.run = reliable_run
    runner_class._run_single = reliable_single
    runner_class._complete_file = complete_with_filename

    # Runtime imports the class object, so method patches apply immediately.
    runtime.HTTPTransferRunner = runner_class


__all__ = [
    "infer_filename",
    "normalize_user_filename",
    "install_reliability",
]
