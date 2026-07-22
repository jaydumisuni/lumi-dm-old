"""SQLite-backed task, queue, event and resume storage for Lumi DM v2."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator

from .models import DownloadTask, TaskStatus, utc_now


_SCHEMA_VERSION = 2


class StateStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.resume_dir = self.data_dir / "resume"
        self.resume_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir = self.data_dir / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "lumi.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _migrate(self) -> None:
        with self.transaction() as conn:
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current < 1:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        task_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        queue_id TEXT NOT NULL,
                        priority INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_tasks_status
                        ON tasks(status, queue_id, priority DESC, created_at ASC);

                    CREATE TABLE IF NOT EXISTS queues (
                        id TEXT PRIMARY KEY,
                        queue_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_events_task
                        ON events(task_id, seq DESC);
                    """
                )
                current = 1
            if current < 2:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                current = 2
            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

        if self.get_queue("default") is None:
            self.save_queue(
                {
                    "id": "default",
                    "name": "Main queue",
                    "active": True,
                    "max_running": 0,
                    "speed_limit_bps": 0,
                    "scheduled_start": "",
                    "scheduled_stop": "",
                    "stop_when_empty": False,
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )

    def save_task(self, task: DownloadTask) -> None:
        task.touch()
        payload = json.dumps(task.to_dict(), separators=(",", ":"), sort_keys=True)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tasks(
                    id, task_json, status, queue_id, priority, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    task_json=excluded.task_json,
                    status=excluded.status,
                    queue_id=excluded.queue_id,
                    priority=excluded.priority,
                    updated_at=excluded.updated_at
                """,
                (
                    task.id,
                    payload,
                    task.status,
                    task.queue_id,
                    int(task.priority),
                    task.created_at,
                    task.updated_at,
                ),
            )

    def get_task(self, task_id: str) -> DownloadTask | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT task_json FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return DownloadTask.from_dict(json.loads(row["task_json"]))

    def list_tasks(
        self,
        *,
        statuses: set[str] | None = None,
        queue_id: str | None = None,
        limit: int = 500,
        oldest_first: bool = False,
    ) -> list[DownloadTask]:
        where: list[str] = []
        values: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where.append(f"status IN ({placeholders})")
            values.extend(sorted(statuses))
        if queue_id:
            where.append("queue_id=?")
            values.append(queue_id)
        sql = "SELECT task_json FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        direction = "ASC" if oldest_first else "DESC"
        sql += f" ORDER BY priority DESC, created_at {direction} LIMIT ?"
        values.append(max(1, min(5000, int(limit))))
        with self._lock:
            rows = self._conn.execute(sql, values).fetchall()
        return [DownloadTask.from_dict(json.loads(row["task_json"])) for row in rows]

    def delete_task(self, task_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self.delete_resume(task_id)

    def update_task(self, task_id: str, **changes: Any) -> DownloadTask:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        for key, value in changes.items():
            if not hasattr(task, key):
                raise AttributeError(key)
            setattr(task, key, value)
        self.save_task(task)
        return task

    def recover_incomplete(self) -> int:
        recoverable = {
            TaskStatus.RUNNING.value,
            TaskStatus.RESOLVING.value,
            TaskStatus.QUEUED.value,
            TaskStatus.PAUSING.value,
            TaskStatus.CANCELLING.value,
            TaskStatus.VERIFYING.value,
            TaskStatus.POST_PROCESSING.value,
        }
        count = 0
        for task in self.list_tasks(statuses=recoverable, limit=5000):
            previous_status = task.status
            task.status = TaskStatus.PAUSED.value
            task.error = ""
            task.error_code = ""
            self.save_task(task)
            self.append_event(
                task.id,
                "recovered_after_restart",
                {"previous_status": previous_status},
            )
            count += 1
        return count

    def save_queue(self, queue: dict[str, Any]) -> None:
        queue = dict(queue)
        queue.setdefault("updated_at", utc_now())
        payload = json.dumps(queue, separators=(",", ":"), sort_keys=True)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO queues(id, queue_json, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    queue_json=excluded.queue_json,
                    updated_at=excluded.updated_at
                """,
                (str(queue["id"]), payload, str(queue["updated_at"])),
            )

    def get_queue(self, queue_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT queue_json FROM queues WHERE id=?",
                (queue_id,),
            ).fetchone()
        return json.loads(row["queue_json"]) if row else None

    def list_queues(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT queue_json FROM queues ORDER BY id"
            ).fetchall()
        return [json.loads(row["queue_json"]) for row in rows]

    def delete_queue(self, queue_id: str) -> None:
        if queue_id == "default":
            raise ValueError("The default queue cannot be deleted")
        if self.get_queue(queue_id) is None:
            raise KeyError(queue_id)
        tasks = self.list_tasks(queue_id=queue_id, limit=5000)
        with self.transaction() as conn:
            for task in tasks:
                task.queue_id = "default"
                task.touch()
                conn.execute(
                    """
                    UPDATE tasks SET task_json=?, queue_id=?, updated_at=? WHERE id=?
                    """,
                    (
                        json.dumps(task.to_dict(), separators=(",", ":"), sort_keys=True),
                        "default",
                        task.updated_at,
                        task.id,
                    ),
                )
            conn.execute("DELETE FROM queues WHERE id=?", (queue_id,))

    def append_event(
        self,
        task_id: str | None,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO events(task_id, event_type, payload_json, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (
                    task_id,
                    event_type,
                    json.dumps(payload or {}, separators=(",", ":"), sort_keys=True),
                    utc_now(),
                ),
            )

    def list_events(self, task_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT event_type, payload_json, created_at
                FROM events WHERE task_id=?
                ORDER BY seq DESC LIMIT ?
                """,
                (task_id, max(1, min(1000, int(limit)))),
            ).fetchall()
        return [
            {
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def set_setting(self, key: str, value: Any) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value_json, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at
                """,
                (key, json.dumps(value), utc_now()),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute(
                "SELECT value_json FROM settings WHERE key=?",
                (key,),
            ).fetchone()
        return json.loads(row["value_json"]) if row else default

    def save_resume(self, task_id: str, payload: dict[str, Any]) -> None:
        target = self.resume_dir / f"{task_id}.json"
        temporary = target.with_suffix(".json.tmp")
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        temporary.write_text(data, encoding="utf-8")
        os.replace(temporary, target)

    def load_resume(self, task_id: str) -> dict[str, Any] | None:
        target = self.resume_dir / f"{task_id}.json"
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            damaged = self.backup_dir / f"{task_id}.resume-damaged.json"
            try:
                os.replace(target, damaged)
            except OSError:
                pass
            return None

    def delete_resume(self, task_id: str) -> None:
        try:
            (self.resume_dir / f"{task_id}.json").unlink(missing_ok=True)
        except OSError:
            pass

    def import_legacy_json(self, legacy_path: Path) -> int:
        """Import the old downloads.json once, without trusting active states."""
        legacy_path = Path(legacy_path)
        marker = self.data_dir / ".legacy-imported"
        if marker.exists() or not legacy_path.exists():
            return 0
        try:
            raw = json.loads(legacy_path.read_text(encoding="utf-8"))
        except Exception:
            marker.write_text("invalid", encoding="utf-8")
            return 0

        imported = 0
        for task_id, value in dict(raw).items():
            try:
                value = dict(value)
                value.setdefault("id", task_id)
                value.setdefault("type", "http")
                value.setdefault("status", TaskStatus.PAUSED.value)
                if value["status"] not in {
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                    TaskStatus.PAUSED.value,
                }:
                    value["status"] = TaskStatus.PAUSED.value
                value.setdefault("filename", "download.bin")
                value.setdefault("target_dir", str(self.data_dir))
                value.setdefault("temp_dir", value["target_dir"])
                value.setdefault("final_path", value.get("path", ""))
                value.setdefault("partial_path", value.get("partial_path", ""))
                value.setdefault(
                    "request",
                    {
                        "url": value.get("url", ""),
                        "final_url": value.get("final_url", ""),
                    },
                )
                self.save_task(DownloadTask.from_dict(value))
                imported += 1
            except Exception:
                continue
        marker.write_text(str(imported), encoding="utf-8")
        return imported
