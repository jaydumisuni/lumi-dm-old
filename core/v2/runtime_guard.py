"""Concurrency guards for the Lumi task launch boundary.

The queue controller reserves a slot before calling the runtime starter. A user can
pause, cancel or delete a task during that handoff. These guards make the launch
conditional on the task still being queued and release reservations idempotently.
"""
from __future__ import annotations

from .models import TaskStatus
from .queueing import QueueController
from .runtime import LumiRuntime


def _install_queue_release_guard() -> None:
    if getattr(QueueController, "_lumi_release_guard", False):
        return

    def task_finished(self: QueueController, task_id: str, queue_id: str) -> None:
        del queue_id  # A task may have moved queues during the worker handoff.
        with self._lock:
            self._running.discard(task_id)
            for current_queue, group in list(self._running_by_queue.items()):
                group.discard(task_id)
                if not group:
                    self._running_by_queue.pop(current_queue, None)
        self.wake()

    QueueController.task_finished = task_finished
    QueueController._lumi_release_guard = True


def _install_runtime_launch_guard() -> None:
    if getattr(LumiRuntime, "_lumi_launch_guard", False):
        return

    original_start = LumiRuntime._start_task
    original_run = LumiRuntime._run_task

    def guarded_start(self: LumiRuntime, task_id: str) -> None:
        task = self.store.get_task(task_id)
        if task is None or task.status != TaskStatus.QUEUED.value:
            self.queue.task_finished(
                task_id,
                task.queue_id if task is not None else "default",
            )
            return
        original_start(self, task_id)

    def guarded_run(
        self: LumiRuntime,
        task_id: str,
        pause_event,
        cancel_event,
    ) -> None:
        task = self.store.get_task(task_id)
        if task is None or task.status != TaskStatus.QUEUED.value:
            with self._lock:
                self._controls.pop(task_id, None)
                self._backend_ids.pop(task_id, None)
            self.queue.task_finished(
                task_id,
                task.queue_id if task is not None else "default",
            )
            self._maybe_completion_action()
            return
        original_run(self, task_id, pause_event, cancel_event)

    LumiRuntime._start_task = guarded_start
    LumiRuntime._run_task = guarded_run
    LumiRuntime._lumi_launch_guard = True


def install() -> None:
    _install_queue_release_guard()
    _install_runtime_launch_guard()


install()
