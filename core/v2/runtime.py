"""Application runtime facade for Lumi DM v2.

This module keeps Flask and Electron unaware of storage, queue or transport details.
It intentionally exports the same public functions as the legacy engine while adding
persistent queues, request envelopes, link repair and crash-safe HTTP recovery.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import threading
import time
import uuid
from typing import Any

from core import engine as legacy

from .http_transfer import HTTPTransferRunner, probe_resource
from .models import (
    DownloadTask,
    RequestEnvelope,
    TaskStatus,
    TaskType,
    TERMINAL_STATUSES,
    utc_now,
)
from .queueing import QueueController
from .store import StateStore


class LumiRuntime:
    def __init__(self, data_dir: Path, legacy_path: Path | None = None):
        self.data_dir = Path(data_dir)
        self.store = StateStore(self.data_dir)
        if legacy_path:
            self.store.import_legacy_json(legacy_path)
        self.store.recover_incomplete()
        self._controls: dict[str, tuple[threading.Event, threading.Event]] = {}
        self._backend_ids: dict[str, str] = {}
        self._lock = threading.RLock()
        max_running = int(self.store.get_setting("max_concurrent", 8))
        self.queue = QueueController(
            self.store,
            self._start_task,
            max_running=max_running,
        )
        self.default_connections = max(
            1, int(self.store.get_setting("default_connections", 8))
        )
        self.completion_action = str(
            self.store.get_setting("completion_action", "none")
        )

    def close(self) -> None:
        self.queue.close()
        self.store.close()

    def _new_id(self) -> str:
        return uuid.uuid4().hex

    def _unique_destination(
        self,
        target_dir: Path,
        filename: str,
        *,
        policy: str,
    ) -> tuple[Path, str]:
        filename = Path(filename or "download.bin").name
        final = target_dir / filename
        if not final.exists():
            return final, filename
        if policy == "overwrite":
            return final, filename
        if policy == "reject":
            raise FileExistsError(f"File already exists: {final}")
        stem, suffix = final.stem, final.suffix
        index = 2
        while final.exists():
            final = target_dir / f"{stem} ({index}){suffix}"
            index += 1
        return final, final.name

    def create_http_task(
        self,
        url: str,
        *,
        target_dir: Path,
        filename: str = "",
        overwrite: bool = False,
        resume: bool = True,
        connections: int = 0,
        max_speed_bps: int = 0,
        queue_id: str = "default",
        priority: int = 0,
        start_paused: bool = False,
        request_envelope: dict[str, Any] | RequestEnvelope | None = None,
        temp_dir: Path | None = None,
        duplicate_policy: str = "",
    ) -> DownloadTask:
        cleaned = str(url or "").strip()
        if not re.match(r"^(https?|ftp)://", cleaned, re.I):
            raise ValueError("url must be http, https, or ftp")
        if self.store.get_queue(queue_id) is None:
            raise KeyError(f"Unknown queue: {queue_id}")

        envelope = (
            request_envelope
            if isinstance(request_envelope, RequestEnvelope)
            else RequestEnvelope.from_dict(request_envelope)
        )
        if not envelope.url:
            envelope.url = cleaned
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(temp_dir or target_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)

        suggested = (
            filename.strip()
            or envelope.suggested_filename.strip()
            or Path(cleaned.split("?", 1)[0]).name
            or "download.bin"
        )
        policy = duplicate_policy or ("overwrite" if overwrite else "rename")
        final, final_name = self._unique_destination(
            target_dir,
            suggested,
            policy=policy,
        )
        if policy == "overwrite":
            final.unlink(missing_ok=True)
        partial = temp_dir / f"{final_name}.part"
        if policy == "overwrite":
            partial.unlink(missing_ok=True)

        task = DownloadTask(
            id=self._new_id(),
            type=(
                TaskType.FTP.value
                if cleaned.lower().startswith("ftp://")
                else TaskType.HTTP.value
            ),
            status=(
                TaskStatus.PAUSED.value
                if start_paused
                else TaskStatus.QUEUED.value
            ),
            request=envelope,
            filename=final_name,
            target_dir=str(target_dir),
            temp_dir=str(temp_dir),
            final_path=str(final),
            partial_path=str(partial),
            queue_id=queue_id,
            priority=int(priority),
            connections=max(
                1,
                min(128, int(connections or self.default_connections)),
            ),
            max_speed_bps=max(0, int(max_speed_bps or 0)),
            duplicate_policy=policy,
            metadata={"resume_enabled": bool(resume)},
        )
        self.store.save_task(task)
        self.store.append_event(
            task.id,
            "created",
            {"type": task.type, "queue_id": task.queue_id},
        )
        self.queue.wake()
        return task

    def create_delegated_task(
        self,
        task_type: str,
        url: str,
        *,
        target_dir: Path,
        metadata: dict[str, Any],
        queue_id: str = "default",
        priority: int = 0,
        start_paused: bool = False,
    ) -> DownloadTask:
        if self.store.get_queue(queue_id) is None:
            raise KeyError(f"Unknown queue: {queue_id}")
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        task = DownloadTask(
            id=self._new_id(),
            type=task_type,
            status=(
                TaskStatus.PAUSED.value
                if start_paused
                else TaskStatus.QUEUED.value
            ),
            request=RequestEnvelope(url=url),
            filename=str(metadata.get("filename") or "Resolving…"),
            target_dir=str(target_dir),
            temp_dir=str(target_dir),
            final_path=str(target_dir),
            partial_path="",
            queue_id=queue_id,
            priority=int(priority),
            connections=max(
                1,
                int(metadata.get("connections") or self.default_connections),
            ),
            metadata=dict(metadata),
        )
        self.store.save_task(task)
        self.store.append_event(task.id, "created", {"type": task.type})
        self.queue.wake()
        return task

    def _start_task(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        if task is None:
            return
        pause_event = threading.Event()
        cancel_event = threading.Event()
        with self._lock:
            self._controls[task_id] = (pause_event, cancel_event)
        thread = threading.Thread(
            target=self._run_task,
            args=(task_id, pause_event, cancel_event),
            name=f"lumi-task-{task_id[:8]}",
            daemon=True,
        )
        thread.start()

    def _run_task(
        self,
        task_id: str,
        pause_event: threading.Event,
        cancel_event: threading.Event,
    ) -> None:
        task = self.store.get_task(task_id)
        if task is None:
            return
        try:
            if task.type == TaskType.HTTP.value:
                runner = HTTPTransferRunner(
                    self.store,
                    task_id,
                    pause_event=pause_event,
                    cancel_event=cancel_event,
                    update_callback=lambda _current: None,
                )
                runner.run()
            elif task.type in {
                TaskType.FTP.value,
                TaskType.TORRENT.value,
                TaskType.VIDEO.value,
            }:
                self._run_legacy_backend(task, pause_event, cancel_event)
            else:
                task.status = TaskStatus.FAILED.value
                task.error = f"Unsupported task type: {task.type}"
                task.error_code = "unsupported_type"
                task.finished_at = utc_now()
                self.store.save_task(task)
        finally:
            current = self.store.get_task(task_id)
            queue_id = current.queue_id if current else task.queue_id
            with self._lock:
                self._controls.pop(task_id, None)
                self._backend_ids.pop(task_id, None)
            self.queue.task_finished(task_id, queue_id)
            self._maybe_completion_action()

    def _run_legacy_backend(
        self,
        task: DownloadTask,
        pause_event: threading.Event,
        cancel_event: threading.Event,
    ) -> None:
        try:
            if task.type == TaskType.TORRENT.value:
                backend = legacy.start_torrent(
                    task.request.url,
                    target_dir=Path(task.target_dir),
                    connections=task.connections,
                )
            elif task.type == TaskType.VIDEO.value:
                backend = legacy.start_video(
                    task.request.url,
                    target_dir=Path(task.target_dir),
                    format_id=str(
                        task.metadata.get("format_id")
                        or "bestvideo+bestaudio/best"
                    ),
                    audio_only=bool(task.metadata.get("audio_only")),
                    subtitles=bool(task.metadata.get("subtitles")),
                )
            else:
                backend = legacy.start_http(
                    task.request.url,
                    target_dir=Path(task.target_dir),
                    filename=task.filename,
                    overwrite=task.duplicate_policy == "overwrite",
                    resume=bool(task.metadata.get("resume_enabled", True)),
                    connections=1,
                    max_speed_bps=task.max_speed_bps,
                )
        except Exception as exc:
            task.status = TaskStatus.FAILED.value
            task.error = str(exc)
            task.error_code = "backend_start_failed"
            task.finished_at = utc_now()
            self.store.save_task(task)
            return

        backend_id = str(backend["id"])
        with self._lock:
            self._backend_ids[task.id] = backend_id
        task.backend_id = backend_id
        task.status = TaskStatus.RUNNING.value
        task.started_at = task.started_at or utc_now()
        self.store.save_task(task)

        while True:
            if cancel_event.is_set():
                legacy.cancel_job(backend_id)
            if pause_event.is_set():
                legacy.pause_job(backend_id)
            state = legacy.get_job(backend_id)
            mapped = self._copy_backend_state(task, state)
            self.store.save_task(mapped)
            if mapped.status in TERMINAL_STATUSES | {
                TaskStatus.PAUSED.value,
                TaskStatus.NEEDS_LINK.value,
            }:
                return
            time.sleep(0.5)

    @staticmethod
    def _copy_backend_state(
        task: DownloadTask,
        state: dict[str, Any],
    ) -> DownloadTask:
        status = str(state.get("status") or task.status)
        if status == "unknown":
            status = TaskStatus.FAILED.value
        task.status = status
        task.filename = str(state.get("filename") or task.filename)
        task.final_path = str(state.get("path") or task.final_path)
        task.partial_path = str(state.get("partial_path") or task.partial_path)
        task.total_bytes = int(state.get("total_bytes") or task.total_bytes or 0)
        task.downloaded_bytes = int(
            state.get("downloaded_bytes") or task.downloaded_bytes or 0
        )
        task.progress_percent = float(
            state.get("progress_percent") or task.progress_percent or 0
        )
        task.speed_bytes_per_sec = float(
            state.get("speed_bytes_per_sec") or 0
        )
        task.error = str(state.get("error") or "")
        task.mode = str(state.get("mode") or task.mode)
        task.started_at = str(state.get("started_at") or task.started_at)
        task.finished_at = str(state.get("finished_at") or task.finished_at)
        return task

    def get_task(self, task_id: str) -> DownloadTask | None:
        return self.store.get_task(task_id)

    def list_tasks(self, limit: int = 50) -> list[DownloadTask]:
        return self.store.list_tasks(limit=limit)

    def pause(self, task_id: str) -> DownloadTask | None:
        task = self.store.get_task(task_id)
        if task is None:
            return None
        if task.status == TaskStatus.QUEUED.value:
            task.status = TaskStatus.PAUSED.value
            self.store.save_task(task)
            self.store.append_event(task.id, "paused_before_start")
            return task
        if task.status not in {
            TaskStatus.RUNNING.value,
            TaskStatus.RESOLVING.value,
        }:
            return task
        task.status = TaskStatus.PAUSING.value
        self.store.save_task(task)
        with self._lock:
            controls = self._controls.get(task_id)
        if controls:
            controls[0].set()
        return task

    def resume(self, task_id: str) -> DownloadTask | None:
        task = self.store.get_task(task_id)
        if task is None:
            return None
        if task.status not in {
            TaskStatus.PAUSED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.NEEDS_LINK.value,
        }:
            return task
        if task.status == TaskStatus.NEEDS_LINK.value:
            return task
        task.status = TaskStatus.QUEUED.value
        task.error = ""
        task.error_code = ""
        task.finished_at = ""
        self.store.save_task(task)
        self.store.append_event(task.id, "queued_for_resume")
        self.queue.wake()
        return task

    def cancel(self, task_id: str) -> DownloadTask | None:
        task = self.store.get_task(task_id)
        if task is None:
            return None
        if task.status == TaskStatus.QUEUED.value:
            task.status = TaskStatus.CANCELLED.value
            task.finished_at = utc_now()
            self.store.save_task(task)
            return task
        if task.status in TERMINAL_STATUSES:
            return task
        task.status = TaskStatus.CANCELLING.value
        self.store.save_task(task)
        with self._lock:
            controls = self._controls.get(task_id)
        if controls:
            controls[1].set()
        return task

    def repair_link(
        self,
        task_id: str,
        envelope_value: dict[str, Any],
    ) -> DownloadTask:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        envelope = RequestEnvelope.from_dict(envelope_value)
        if not envelope.url:
            raise ValueError("replacement request requires url")
        probe = probe_resource(envelope)
        if (
            task.total_bytes
            and probe.total_bytes
            and task.total_bytes != probe.total_bytes
        ):
            raise ValueError(
                "Replacement size mismatch: "
                f"expected {task.total_bytes}, got {probe.total_bytes}"
            )
        if task.etag and probe.etag and task.etag != probe.etag:
            raise ValueError("Replacement ETag does not match the existing task")
        envelope.final_url = probe.final_url
        task.request = envelope
        task.status = TaskStatus.QUEUED.value
        task.error = ""
        task.error_code = ""
        task.finished_at = ""
        self.store.save_task(task)
        self.store.append_event(
            task.id,
            "download_link_repaired",
            {"final_url": probe.final_url},
        )
        self.queue.wake()
        return task

    def delete(self, task_id: str, *, delete_file: bool) -> bool:
        task = self.store.get_task(task_id)
        if task is None:
            return False
        if task.status not in TERMINAL_STATUSES | {
            TaskStatus.PAUSED.value,
            TaskStatus.NEEDS_LINK.value,
            TaskStatus.STAGED.value,
        }:
            raise RuntimeError("Pause or cancel the task before deleting it")
        if delete_file:
            for value in (task.final_path, task.partial_path):
                if value:
                    try:
                        path = Path(value)
                        if path.is_file():
                            path.unlink(missing_ok=True)
                    except OSError:
                        pass
        self.store.delete_task(task_id)
        return True

    def verify(
        self,
        task_id: str,
        expected: str,
        algorithm: str,
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            return {"status": "unknown", "id": task_id}
        path = Path(task.final_path)
        if not path.is_file():
            return {
                "status": "failed",
                "id": task_id,
                "error": "file not found",
            }
        try:
            hasher = hashlib.new(algorithm.lower())
        except ValueError:
            return {
                "status": "failed",
                "id": task_id,
                "error": "unsupported hash",
            }
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                hasher.update(chunk)
        actual = hasher.hexdigest()
        return {
            "status": (
                "ok"
                if actual.lower() == expected.lower()
                else "mismatch"
            ),
            "id": task_id,
            "algorithm": algorithm.lower(),
            "expected": expected,
            "actual": actual,
        }

    def stage(
        self,
        url: str,
        *,
        target_dir: Path,
        filename: str,
        download_type: str,
    ) -> DownloadTask:
        dtype = download_type
        if dtype == "auto":
            dtype = (
                TaskType.TORRENT.value
                if url.startswith("magnet:") or url.lower().endswith(".torrent")
                else TaskType.HTTP.value
            )
        if dtype != TaskType.HTTP.value:
            task = self.create_delegated_task(
                dtype,
                url,
                target_dir=target_dir,
                metadata={"filename": filename or "Resolving…"},
                start_paused=True,
            )
            task.status = TaskStatus.STAGED.value
            self.store.save_task(task)
            return task
        envelope = RequestEnvelope(url=url, suggested_filename=filename)
        probed_name = filename
        total = 0
        final_url = ""
        try:
            probe = probe_resource(envelope)
            probed_name = probed_name or probe.filename
            total = probe.total_bytes
            final_url = probe.final_url
        except Exception:
            pass
        task = self.create_http_task(
            url,
            target_dir=target_dir,
            filename=probed_name,
            start_paused=True,
            request_envelope=envelope,
        )
        task.status = TaskStatus.STAGED.value
        task.total_bytes = total
        task.request.final_url = final_url
        self.store.save_task(task)
        return task

    def confirm_staged(
        self,
        task_id: str,
        *,
        filename: str,
        target_dir: str,
        connections: int,
    ) -> DownloadTask:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task.status != TaskStatus.STAGED.value:
            raise ValueError("Task is not staged")
        if target_dir:
            target = Path(target_dir)
            target.mkdir(parents=True, exist_ok=True)
            task.target_dir = str(target)
            task.final_path = str(target / (filename or task.filename))
        if filename:
            task.filename = Path(filename).name
            task.final_path = str(Path(task.target_dir) / task.filename)
            task.partial_path = str(
                Path(task.temp_dir) / f"{task.filename}.part"
            )
        if connections:
            task.connections = max(1, min(128, int(connections)))
        task.status = TaskStatus.QUEUED.value
        self.store.save_task(task)
        self.queue.wake()
        return task

    def set_max_concurrent(self, value: int) -> int:
        result = self.queue.set_max_running(value)
        self.store.set_setting("max_concurrent", result)
        return result

    def set_default_connections(self, value: int) -> int:
        self.default_connections = max(1, min(128, int(value)))
        self.store.set_setting(
            "default_connections",
            self.default_connections,
        )
        return self.default_connections

    def set_completion_action(self, action: str) -> str:
        allowed = {"none", "sleep", "shutdown", "restart"}
        self.completion_action = action if action in allowed else "none"
        self.store.set_setting("completion_action", self.completion_action)
        return self.completion_action

    def _maybe_completion_action(self) -> None:
        if self.completion_action == "none":
            return
        active = self.store.list_tasks(
            statuses={
                TaskStatus.QUEUED.value,
                TaskStatus.RESOLVING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.PAUSING.value,
            },
            limit=1,
        )
        if active:
            return
        legacy.set_completion_action(self.completion_action)
        legacy._run_completion_action(self.completion_action)


_RUNTIME: LumiRuntime | None = None
_RUNTIME_LOCK = threading.Lock()


def _require_runtime() -> LumiRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is None:
            default = Path(os.environ.get("LUMIDM_DATA_DIR", ".lumi-data"))
            _RUNTIME = LumiRuntime(default)
        return _RUNTIME


def load_state(persist_path: Path) -> None:
    global _RUNTIME
    persist_path = Path(persist_path)
    with _RUNTIME_LOCK:
        if _RUNTIME is not None:
            return
        _RUNTIME = LumiRuntime(persist_path.parent, legacy_path=persist_path)


def start_http(
    url: str,
    *,
    target_dir: Path,
    filename: str = "",
    overwrite: bool = False,
    resume: bool = True,
    connections: int = 0,
    max_speed_bps: int = 0,
    queue_id: str = "default",
    priority: int = 0,
    start_paused: bool = False,
    request_envelope: dict[str, Any] | None = None,
    temp_dir: Path | None = None,
    duplicate_policy: str = "",
) -> dict[str, Any]:
    return _require_runtime().create_http_task(
        url,
        target_dir=target_dir,
        filename=filename,
        overwrite=overwrite,
        resume=resume,
        connections=connections,
        max_speed_bps=max_speed_bps,
        queue_id=queue_id,
        priority=priority,
        start_paused=start_paused,
        request_envelope=request_envelope,
        temp_dir=temp_dir,
        duplicate_policy=duplicate_policy,
    ).to_dict(public=True)


def start_torrent(
    url: str,
    *,
    target_dir: Path,
    connections: int = 0,
    queue_id: str = "default",
    priority: int = 0,
    start_paused: bool = False,
) -> dict[str, Any]:
    task = _require_runtime().create_delegated_task(
        TaskType.TORRENT.value,
        url,
        target_dir=target_dir,
        metadata={
            "filename": (
                url[:60]
                if url.startswith("magnet:")
                else Path(url).name
            ),
            "connections": connections,
        },
        queue_id=queue_id,
        priority=priority,
        start_paused=start_paused,
    )
    return task.to_dict(public=True)


def start_video(
    url: str,
    *,
    target_dir: Path,
    format_id: str = "bestvideo+bestaudio/best",
    audio_only: bool = False,
    subtitles: bool = False,
    queue_id: str = "default",
    priority: int = 0,
    start_paused: bool = False,
) -> dict[str, Any]:
    task = _require_runtime().create_delegated_task(
        TaskType.VIDEO.value,
        url,
        target_dir=target_dir,
        metadata={
            "filename": "Fetching title…",
            "format_id": format_id,
            "audio_only": audio_only,
            "subtitles": subtitles,
        },
        queue_id=queue_id,
        priority=priority,
        start_paused=start_paused,
    )
    return task.to_dict(public=True)


def get_video_formats(url: str) -> dict[str, Any]:
    return legacy.get_video_formats(url)


def get_capabilities() -> dict[str, Any]:
    caps = legacy.get_capabilities()
    runtime = _require_runtime()
    caps.update(
        {
            "version": "2.0.0-dev",
            "max_concurrent": runtime.queue.max_running,
            "persistent_queues": True,
            "resume_journal": True,
            "repair_download_link": True,
            "request_envelope": True,
        }
    )
    return caps


def get_job(job_id: str) -> dict[str, Any]:
    task = _require_runtime().get_task(job_id)
    return (
        task.to_dict(public=True)
        if task
        else {"status": "unknown", "id": job_id}
    )


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    return [
        task.to_dict(public=True)
        for task in _require_runtime().list_tasks(limit)
    ]


def pause_job(job_id: str) -> dict[str, Any]:
    task = _require_runtime().pause(job_id)
    return (
        task.to_dict(public=True)
        if task
        else {"status": "unknown", "id": job_id}
    )


def resume_job(job_id: str) -> dict[str, Any]:
    task = _require_runtime().resume(job_id)
    return (
        task.to_dict(public=True)
        if task
        else {"status": "unknown", "id": job_id}
    )


def retry_job(job_id: str) -> dict[str, Any]:
    return resume_job(job_id)


def cancel_job(job_id: str) -> dict[str, Any]:
    task = _require_runtime().cancel(job_id)
    return (
        task.to_dict(public=True)
        if task
        else {"status": "unknown", "id": job_id}
    )


def delete_job(job_id: str, delete_file: bool = False) -> dict[str, Any]:
    runtime = _require_runtime()
    try:
        deleted = runtime.delete(job_id, delete_file=delete_file)
    except RuntimeError as exc:
        return {"status": "error", "id": job_id, "error": str(exc)}
    return (
        {"status": "deleted", "id": job_id}
        if deleted
        else {"status": "unknown", "id": job_id}
    )


def verify_checksum(
    job_id: str,
    expected: str,
    algo: str = "sha256",
) -> dict[str, Any]:
    return _require_runtime().verify(job_id, expected, algo)


def stage_download(
    url: str,
    *,
    target_dir: Path | None = None,
    filename: str = "",
    download_type: str = "auto",
) -> dict[str, Any]:
    target = target_dir or Path.home() / "Downloads"
    return _require_runtime().stage(
        url,
        target_dir=Path(target),
        filename=filename,
        download_type=download_type,
    ).to_dict(public=True)


def confirm_staged(
    job_id: str,
    *,
    filename: str = "",
    target_dir: str = "",
    connections: int = 0,
) -> dict[str, Any]:
    return _require_runtime().confirm_staged(
        job_id,
        filename=filename,
        target_dir=target_dir,
        connections=connections,
    ).to_dict(public=True)


def pause_all() -> dict[str, int]:
    count = 0
    for task in _require_runtime().list_tasks(5000):
        if task.status in {
            TaskStatus.QUEUED.value,
            TaskStatus.RESOLVING.value,
            TaskStatus.RUNNING.value,
        }:
            pause_job(task.id)
            count += 1
    return {"paused": count}


def resume_all() -> dict[str, int]:
    count = 0
    for task in _require_runtime().list_tasks(5000):
        if task.status in {
            TaskStatus.PAUSED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        }:
            resume_job(task.id)
            count += 1
    return {"resumed": count}


def cancel_all() -> dict[str, int]:
    count = 0
    for task in _require_runtime().list_tasks(5000):
        if task.status not in TERMINAL_STATUSES:
            cancel_job(task.id)
            count += 1
    return {"cancelled": count}


def clear_done() -> dict[str, int]:
    runtime = _require_runtime()
    removed = 0
    for task in runtime.list_tasks(5000):
        if task.status in TERMINAL_STATUSES | {TaskStatus.STAGED.value}:
            runtime.delete(task.id, delete_file=False)
            removed += 1
    return {"cleared": removed}


def set_max_concurrent(value: int) -> int:
    return _require_runtime().set_max_concurrent(value)


def set_default_connections(value: int) -> int:
    return _require_runtime().set_default_connections(value)


def get_default_connections() -> int:
    return _require_runtime().default_connections


def set_completion_action(action: str) -> str:
    return _require_runtime().set_completion_action(action)


def get_completion_action() -> str:
    return _require_runtime().completion_action


def repair_download_link(
    job_id: str,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    return _require_runtime().repair_link(job_id, envelope).to_dict(public=True)


def list_queues() -> list[dict[str, Any]]:
    return _require_runtime().store.list_queues()


def create_queue(
    name: str,
    queue_id: str,
    max_running: int = 0,
    active: bool = True,
) -> dict[str, Any]:
    return _require_runtime().queue.create_queue(
        name,
        queue_id=queue_id,
        max_running=max_running,
        active=active,
    )


def update_queue(queue_id: str, **changes: Any) -> dict[str, Any]:
    return _require_runtime().queue.update_queue(queue_id, **changes)


def delete_queue(queue_id: str) -> None:
    _require_runtime().queue.delete_queue(queue_id)


def move_task_to_queue(task_id: str, queue_id: str) -> dict[str, Any]:
    runtime = _require_runtime()
    runtime.queue.move_task(task_id, queue_id)
    task = runtime.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    return task.to_dict(public=True)


def set_task_priority(task_id: str, priority: int) -> dict[str, Any]:
    runtime = _require_runtime()
    runtime.queue.set_task_priority(task_id, priority)
    task = runtime.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    return task.to_dict(public=True)


def task_events(task_id: str, limit: int = 200) -> list[dict[str, Any]]:
    return _require_runtime().store.list_events(task_id, limit)
