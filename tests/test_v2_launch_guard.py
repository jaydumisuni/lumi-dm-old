from __future__ import annotations

from pathlib import Path
import threading

from core import engine_v2 as _engine_v2  # activates runtime guards
from core.v2.models import TaskStatus
from core.v2.runtime import LumiRuntime


def test_cancelled_task_cannot_launch_during_queue_handoff(tmp_path: Path) -> None:
    runtime = LumiRuntime(tmp_path / "data")
    task = runtime.create_http_task(
        "https://example.invalid/handoff.bin",
        target_dir=tmp_path / "downloads",
        start_paused=True,
    )
    task.status = TaskStatus.CANCELLED.value
    runtime.store.save_task(task)

    runtime.queue.mark_running(task.id, task.queue_id)
    runtime._start_task(task.id)

    assert task.id not in runtime.queue._running
    assert task.id not in runtime._controls
    assert runtime.get_task(task.id).status == TaskStatus.CANCELLED.value
    runtime.close()


def test_worker_rechecks_task_state_and_releases_moved_queue_slot(
    tmp_path: Path,
) -> None:
    runtime = LumiRuntime(tmp_path / "data")
    runtime.queue.create_queue("Secondary", queue_id="secondary", active=False)
    task = runtime.create_http_task(
        "https://example.invalid/worker.bin",
        target_dir=tmp_path / "downloads",
        start_paused=True,
    )

    runtime.queue.mark_running(task.id, "default")
    task.queue_id = "secondary"
    task.status = TaskStatus.PAUSED.value
    runtime.store.save_task(task)
    pause_event = threading.Event()
    cancel_event = threading.Event()
    runtime._controls[task.id] = (pause_event, cancel_event)

    runtime._run_task(task.id, pause_event, cancel_event)

    assert task.id not in runtime.queue._running
    assert all(
        task.id not in group
        for group in runtime.queue._running_by_queue.values()
    )
    assert task.id not in runtime._controls
    assert runtime.get_task(task.id).status == TaskStatus.PAUSED.value
    runtime.close()
