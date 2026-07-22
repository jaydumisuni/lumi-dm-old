"""Persistent named queue controller for Lumi DM v2."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
from typing import Callable, Any

from .models import TaskStatus, utc_now
from .store import StateStore


@dataclass(slots=True)
class QueueConfig:
    id: str
    name: str
    active: bool = True
    max_running: int = 0
    speed_limit_bps: int = 0
    scheduled_start: str = ""
    scheduled_stop: str = ""
    stop_when_empty: bool = False
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "QueueConfig":
        value = dict(value)
        return cls(
            id=str(value["id"]),
            name=str(value.get("name") or value["id"]),
            active=bool(value.get("active", True)),
            max_running=max(0, int(value.get("max_running") or 0)),
            speed_limit_bps=max(0, int(value.get("speed_limit_bps") or 0)),
            scheduled_start=str(value.get("scheduled_start") or ""),
            scheduled_stop=str(value.get("scheduled_stop") or ""),
            stop_when_empty=bool(value.get("stop_when_empty", False)),
            created_at=str(value.get("created_at") or utc_now()),
            updated_at=str(value.get("updated_at") or utc_now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "active": self.active,
            "max_running": self.max_running,
            "speed_limit_bps": self.speed_limit_bps,
            "scheduled_start": self.scheduled_start,
            "scheduled_stop": self.scheduled_stop,
            "stop_when_empty": self.stop_when_empty,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class QueueController:
    """Starts queued tasks fairly while respecting global and per-queue limits."""

    def __init__(
        self,
        store: StateStore,
        starter: Callable[[str], None],
        *,
        max_running: int = 8,
        poll_interval: float = 0.25,
    ):
        self.store = store
        self.starter = starter
        self.max_running = max(1, int(max_running))
        self.poll_interval = max(0.05, float(poll_interval))
        self._running: set[str] = set()
        self._running_by_queue: dict[str, set[str]] = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop,
            name="lumi-queue-controller",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=3)

    def set_max_running(self, value: int) -> int:
        with self._lock:
            self.max_running = max(1, min(128, int(value)))
        self.wake()
        return self.max_running

    def wake(self) -> None:
        self._wake.set()

    def task_finished(self, task_id: str, queue_id: str) -> None:
        with self._lock:
            self._running.discard(task_id)
            group = self._running_by_queue.get(queue_id)
            if group is not None:
                group.discard(task_id)
                if not group:
                    self._running_by_queue.pop(queue_id, None)
        self.wake()

    def mark_running(self, task_id: str, queue_id: str) -> None:
        with self._lock:
            self._running.add(task_id)
            self._running_by_queue.setdefault(queue_id, set()).add(task_id)

    def create_queue(
        self,
        name: str,
        *,
        queue_id: str,
        max_running: int = 0,
        active: bool = True,
    ) -> dict[str, Any]:
        if not queue_id or queue_id == "default":
            raise ValueError("A non-default queue ID is required")
        if self.store.get_queue(queue_id):
            raise ValueError(f"Queue already exists: {queue_id}")
        queue = QueueConfig(
            id=queue_id,
            name=name.strip() or queue_id,
            max_running=max(0, int(max_running)),
            active=bool(active),
        )
        self.store.save_queue(queue.to_dict())
        self.wake()
        return queue.to_dict()

    def update_queue(self, queue_id: str, **changes: Any) -> dict[str, Any]:
        existing = self.store.get_queue(queue_id)
        if existing is None:
            raise KeyError(queue_id)
        queue = QueueConfig.from_dict(existing)
        allowed = {
            "name", "active", "max_running", "speed_limit_bps",
            "scheduled_start", "scheduled_stop", "stop_when_empty",
        }
        for key, value in changes.items():
            if key not in allowed:
                continue
            if key in {"max_running", "speed_limit_bps"}:
                value = max(0, int(value or 0))
            elif key in {"active", "stop_when_empty"}:
                value = bool(value)
            else:
                value = str(value or "")
            setattr(queue, key, value)
        queue.updated_at = utc_now()
        self.store.save_queue(queue.to_dict())
        self.wake()
        return queue.to_dict()

    def delete_queue(self, queue_id: str) -> None:
        self.store.delete_queue(queue_id)
        self.wake()

    def set_task_priority(self, task_id: str, priority: int) -> None:
        self.store.update_task(task_id, priority=int(priority))
        self.wake()

    def move_task(self, task_id: str, queue_id: str) -> None:
        if self.store.get_queue(queue_id) is None:
            raise KeyError(queue_id)
        self.store.update_task(task_id, queue_id=queue_id)
        self.wake()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.poll_interval)
            self._wake.clear()
            try:
                self._apply_schedules()
                self._dispatch()
            except Exception:
                time.sleep(self.poll_interval)

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    def _apply_schedules(self) -> None:
        now = datetime.now(timezone.utc)
        for raw in self.store.list_queues():
            queue = QueueConfig.from_dict(raw)
            changed = False
            start = self._parse_time(queue.scheduled_start)
            stop = self._parse_time(queue.scheduled_stop)
            if start and now >= start and not queue.active:
                queue.active = True
                queue.scheduled_start = ""
                changed = True
            if stop and now >= stop and queue.active:
                queue.active = False
                queue.scheduled_stop = ""
                changed = True
            if changed:
                queue.updated_at = utc_now()
                self.store.save_queue(queue.to_dict())

    def _dispatch(self) -> None:
        with self._lock:
            global_available = self.max_running - len(self._running)
        if global_available <= 0:
            return

        queues = {
            item["id"]: QueueConfig.from_dict(item)
            for item in self.store.list_queues()
        }
        candidates = self.store.list_tasks(
            statuses={TaskStatus.QUEUED.value},
            limit=5000,
            oldest_first=True,
        )
        for task in candidates:
            if global_available <= 0:
                break
            queue = queues.get(task.queue_id) or queues.get("default")
            if queue is None or not queue.active:
                continue
            with self._lock:
                queue_running = len(self._running_by_queue.get(queue.id, set()))
            queue_limit = queue.max_running or self.max_running
            if queue_running >= queue_limit:
                continue

            self.mark_running(task.id, queue.id)
            global_available -= 1
            try:
                self.starter(task.id)
            except Exception as exc:
                self.task_finished(task.id, queue.id)
                task.status = TaskStatus.FAILED.value
                task.error = f"Queue start failed: {exc}"
                task.error_code = "queue_start_failed"
                task.finished_at = utc_now()
                self.store.save_task(task)
                self.store.append_event(
                    task.id,
                    "queue_start_failed",
                    {"error": str(exc)},
                )

        for queue in queues.values():
            if not queue.stop_when_empty or not queue.active:
                continue
            queued = self.store.list_tasks(
                statuses={TaskStatus.QUEUED.value},
                queue_id=queue.id,
                limit=1,
            )
            with self._lock:
                active = bool(self._running_by_queue.get(queue.id))
            if not queued and not active:
                queue.active = False
                queue.updated_at = utc_now()
                self.store.save_queue(queue.to_dict())
