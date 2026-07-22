"""Wave 3 runtime activation for media, torrents and post-processing."""
from __future__ import annotations

import threading

from .runtime_wave2 import *  # noqa: F401,F403 - activates secure HTTP replay
from . import runtime as _runtime
from .media import MediaService
from .models import TaskStatus, TaskType, utc_now
from .postprocess import PostProcessController
from .torrents_safe import TorrentService


def _run_task_wave3(
    self: _runtime.LumiRuntime,
    task_id: str,
    pause_event: threading.Event,
    cancel_event: threading.Event,
) -> None:
    task = self.store.get_task(task_id)
    if task is None:
        return
    try:
        if task.type == TaskType.HTTP.value:
            runner = _runtime.HTTPTransferRunner(
                self.store,
                task_id,
                pause_event=pause_event,
                cancel_event=cancel_event,
                update_callback=lambda _current: None,
            )
            runner.run()
        elif task.type == TaskType.VIDEO.value:
            MediaService(self.store).run(task, pause_event, cancel_event)
        elif task.type == TaskType.TORRENT.value:
            try:
                TorrentService(self.store).run(task, pause_event, cancel_event)
            except Exception as exc:
                task.status = TaskStatus.FAILED.value
                task.error = str(exc)
                task.error_code = "torrent_failed"
                task.finished_at = utc_now()
                self.store.save_task(task)
        elif task.type == TaskType.FTP.value:
            self._run_legacy_backend(task, pause_event, cancel_event)
        else:
            task.status = TaskStatus.FAILED.value
            task.error = f"Unsupported task type: {task.type}"
            task.error_code = "unsupported_type"
            task.finished_at = utc_now()
            self.store.save_task(task)

        current = self.store.get_task(task_id)
        if (
            current is not None
            and current.status == TaskStatus.COMPLETED.value
            and current.post_process
        ):
            PostProcessController(self.store).run(
                current,
                cancel_event=cancel_event,
            )
    except Exception as exc:
        current = self.store.get_task(task_id) or task
        current.status = TaskStatus.FAILED.value
        current.error = str(exc)
        current.error_code = "task_runtime_failed"
        current.finished_at = utc_now()
        self.store.save_task(current)
        self.store.append_event(
            task_id,
            "task_runtime_failed",
            {"error": str(exc)},
        )
    finally:
        current = self.store.get_task(task_id)
        queue_id = current.queue_id if current else task.queue_id
        with self._lock:
            self._controls.pop(task_id, None)
            self._backend_ids.pop(task_id, None)
        self.queue.task_finished(task_id, queue_id)
        self._maybe_completion_action()


_runtime.LumiRuntime._run_task = _run_task_wave3
