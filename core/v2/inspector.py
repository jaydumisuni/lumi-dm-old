"""Task inspection and safe file/task actions for the Lumi product UI."""
from __future__ import annotations

from pathlib import Path
import os
import shutil
from typing import Any

from .models import TaskStatus, TERMINAL_STATUSES, utc_now
from .runtime import LumiRuntime


_ACTIONABLE_IDLE = TERMINAL_STATUSES | {
    TaskStatus.PAUSED.value,
    TaskStatus.NEEDS_LINK.value,
    TaskStatus.STAGED.value,
}


class TaskInspector:
    def __init__(self, runtime: LumiRuntime):
        self.runtime = runtime

    def details(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        final = Path(task.final_path) if task.final_path else None
        partial = Path(task.partial_path) if task.partial_path else None
        resume = self.runtime.store.load_resume(task.id)
        segments = list((resume or {}).get("segments") or [])
        connection_summary = {
            "mode": task.mode,
            "configured": task.connections,
            "segments": segments,
            "segment_count": len(segments),
            "completed_segments": sum(
                1 for item in segments if item.get("status") == "done"
            ),
            "active_segments": sum(
                1 for item in segments if item.get("status") == "active"
            ),
            "journal_saved_at": (resume or {}).get("saved_at", ""),
        }
        file_info = {
            "final": self._path_info(final),
            "partial": self._path_info(partial),
            "target_dir": self._path_info(Path(task.target_dir)),
            "temp_dir": self._path_info(Path(task.temp_dir)),
        }
        return {
            "task": task.to_dict(public=True),
            "overview": {
                "id": task.id,
                "type": task.type,
                "status": task.status,
                "filename": task.filename,
                "progress_percent": task.progress_percent,
                "downloaded_bytes": task.downloaded_bytes,
                "total_bytes": task.total_bytes,
                "speed_bytes_per_sec": task.speed_bytes_per_sec,
                "created_at": task.created_at,
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "error": task.error,
                "error_code": task.error_code,
            },
            "connections": connection_summary,
            "request": task.request.redacted_dict(),
            "queue": {
                "queue_id": task.queue_id,
                "priority": task.priority,
                "category_id": task.category_id,
                "host_profile_id": task.host_profile_id,
                "queue": self.runtime.store.get_queue(task.queue_id),
            },
            "files": file_info,
            "post_processing": {
                "plan": task.post_process,
                "stage": task.metadata.get("post_process_stage", ""),
                "progress": task.metadata.get("post_process_progress", 0),
                "result": {
                    "extraction": task.metadata.get("extraction_result"),
                    "conversion": task.metadata.get("conversion_result"),
                },
            },
            "events": self.runtime.store.list_events(task.id, 300),
            "actions": self.available_actions(task.id),
        }

    def available_actions(self, task_id: str) -> list[str]:
        task = self._task(task_id)
        actions = ["properties", "events"]
        if task.status in {TaskStatus.RUNNING.value, TaskStatus.RESOLVING.value}:
            actions.extend(["pause", "cancel"])
        if task.status in {
            TaskStatus.PAUSED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        }:
            actions.extend(["resume", "restart"])
        if task.status == TaskStatus.NEEDS_LINK.value:
            actions.extend(["repair_link", "restart"])
        if task.status == TaskStatus.COMPLETED.value:
            actions.extend(["open", "open_folder", "move", "rename", "verify", "restart"])
            if task.post_process:
                actions.append("post_process")
        if task.error_code == "missing_file":
            actions.append("locate")
        if task.status in _ACTIONABLE_IDLE:
            actions.extend(["delete_task", "delete_task_and_file"])
        return sorted(set(actions))

    def restart(
        self,
        task_id: str,
        *,
        delete_existing: bool = False,
    ) -> dict[str, Any]:
        task = self._task(task_id)
        if task.status not in _ACTIONABLE_IDLE:
            raise ValueError("Pause or cancel the task before restarting it")
        final = Path(task.final_path) if task.final_path else None
        partial = Path(task.partial_path) if task.partial_path else None
        if partial and partial.is_file():
            partial.unlink(missing_ok=True)
        self.runtime.store.delete_resume(task.id)
        if delete_existing and final and final.is_file():
            final.unlink(missing_ok=True)
        task.status = TaskStatus.QUEUED.value
        task.downloaded_bytes = 0
        task.speed_bytes_per_sec = 0.0
        task.progress_percent = 0.0
        task.started_at = ""
        task.finished_at = ""
        task.error = ""
        task.error_code = ""
        task.etag = ""
        task.last_modified = ""
        task.range_supported = False
        task.backend_id = ""
        self.runtime.store.save_task(task)
        self.runtime.store.append_event(
            task.id,
            "task_restarted",
            {"delete_existing": delete_existing},
        )
        self.runtime.queue.wake()
        return task.to_dict(public=True)

    def move_or_rename(
        self,
        task_id: str,
        destination: str,
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        task = self._task(task_id)
        if task.status != TaskStatus.COMPLETED.value:
            raise ValueError("Only completed files can be moved or renamed")
        source = Path(task.final_path)
        if not source.is_file():
            raise FileNotFoundError(source)
        requested = Path(destination).expanduser()
        if not requested.is_absolute():
            requested = Path(task.target_dir) / requested
        if requested.exists() and requested.is_dir():
            requested = requested / source.name
        requested.parent.mkdir(parents=True, exist_ok=True)
        if requested.exists() and not overwrite:
            requested = self._unique(requested)
        elif requested.exists() and overwrite:
            if requested.is_dir():
                raise IsADirectoryError(requested)
            requested.unlink()
        shutil.move(str(source), str(requested))
        task.final_path = str(requested)
        task.filename = requested.name
        task.target_dir = str(requested.parent)
        self.runtime.store.save_task(task)
        self.runtime.store.append_event(
            task.id,
            "file_moved",
            {"from": str(source), "to": str(requested)},
        )
        return task.to_dict(public=True)

    def locate(self, task_id: str, path: str) -> dict[str, Any]:
        task = self._task(task_id)
        candidate = Path(path).expanduser()
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        if task.total_bytes and candidate.stat().st_size != task.total_bytes:
            raise ValueError(
                "Located file size does not match the completed task "
                f"({candidate.stat().st_size} != {task.total_bytes})"
            )
        task.final_path = str(candidate)
        task.filename = candidate.name
        task.target_dir = str(candidate.parent)
        task.status = TaskStatus.COMPLETED.value
        task.error = ""
        task.error_code = ""
        task.finished_at = task.finished_at or utc_now()
        self.runtime.store.save_task(task)
        self.runtime.store.append_event(
            task.id,
            "missing_file_located",
            {"path": str(candidate)},
        )
        return task.to_dict(public=True)

    def update_placement(
        self,
        task_id: str,
        *,
        queue_id: str | None = None,
        category_id: str | None = None,
        priority: int | None = None,
    ) -> dict[str, Any]:
        task = self._task(task_id)
        if queue_id is not None:
            if self.runtime.store.get_queue(queue_id) is None:
                raise KeyError(queue_id)
            task.queue_id = queue_id
        if category_id is not None:
            task.category_id = category_id
        if priority is not None:
            task.priority = int(priority)
        self.runtime.store.save_task(task)
        self.runtime.store.append_event(
            task.id,
            "task_placement_updated",
            {
                "queue_id": task.queue_id,
                "category_id": task.category_id,
                "priority": task.priority,
            },
        )
        self.runtime.queue.wake()
        return task.to_dict(public=True)

    def delete(self, task_id: str, *, delete_file: bool = False) -> dict[str, Any]:
        task = self._task(task_id)
        if task.status not in _ACTIONABLE_IDLE:
            raise ValueError("Pause or cancel the task before deleting it")
        deleted = self.runtime.delete(task_id, delete_file=delete_file)
        return {
            "status": "deleted" if deleted else "unknown",
            "id": task_id,
            "file_deleted": delete_file,
        }

    def _task(self, task_id: str):
        task = self.runtime.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    @staticmethod
    def _path_info(path: Path | None) -> dict[str, Any] | None:
        if path is None:
            return None
        try:
            exists = path.exists()
            is_file = path.is_file()
            is_dir = path.is_dir()
            return {
                "path": str(path),
                "exists": exists,
                "is_file": is_file,
                "is_dir": is_dir,
                "bytes": path.stat().st_size if is_file else 0,
                "modified_at": (
                    path.stat().st_mtime if exists else None
                ),
                "writable": os.access(path if exists else path.parent, os.W_OK),
            }
        except OSError as exc:
            return {
                "path": str(path),
                "exists": False,
                "error": str(exc),
            }

    @staticmethod
    def _unique(path: Path) -> Path:
        stem, suffix = path.stem, path.suffix
        index = 2
        candidate = path
        while candidate.exists():
            candidate = path.with_name(f"{stem} ({index}){suffix}")
            index += 1
        return candidate
