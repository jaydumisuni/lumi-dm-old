"""Database, storage and file-state maintenance for Lumi DM."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from core.v2.models import TaskStatus, utc_now
from core.v2.store import StateStore


class MaintenanceError(RuntimeError):
    pass


class MaintenanceService:
    def __init__(self, store: StateStore):
        self.store = store
        self.backup_dir = self.store.backup_dir
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def database_health(self) -> dict[str, Any]:
        with self.store._lock:
            integrity_rows = self.store._conn.execute(
                "PRAGMA integrity_check"
            ).fetchall()
            foreign_rows = self.store._conn.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            page_count = int(
                self.store._conn.execute("PRAGMA page_count").fetchone()[0]
            )
            page_size = int(
                self.store._conn.execute("PRAGMA page_size").fetchone()[0]
            )
            user_version = int(
                self.store._conn.execute("PRAGMA user_version").fetchone()[0]
            )
        integrity = [str(row[0]) for row in integrity_rows]
        return {
            "ok": integrity == ["ok"] and not foreign_rows,
            "integrity": integrity,
            "foreign_key_errors": [list(row) for row in foreign_rows],
            "page_count": page_count,
            "page_size": page_size,
            "database_bytes": page_count * page_size,
            "schema_version": user_version,
            "wal_bytes": self._size(self.store.db_path.with_suffix(".db-wal")),
            "shm_bytes": self._size(self.store.db_path.with_suffix(".db-shm")),
        }

    @staticmethod
    def _size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def backup_database(self, label: str = "manual") -> dict[str, Any]:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_label = "".join(
            character
            for character in label.lower().replace(" ", "-")
            if character.isalnum() or character in {"-", "_"}
        )[:40] or "manual"
        destination = self.backup_dir / f"lumi-{safe_label}-{stamp}.db"
        temporary = destination.with_suffix(".db.tmp")
        with self.store._lock:
            self.store._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            backup = sqlite3.connect(temporary)
            try:
                self.store._conn.backup(backup)
                backup.execute("PRAGMA integrity_check")
                backup.commit()
            finally:
                backup.close()
        os.replace(temporary, destination)
        return {
            "path": str(destination),
            "bytes": destination.stat().st_size,
            "created_at": utc_now(),
            "label": safe_label,
        }

    def repair_database(self) -> dict[str, Any]:
        before = self.database_health()
        backup = self.backup_database("before-repair")
        with self.store._lock:
            self.store._conn.execute("PRAGMA wal_checkpoint(FULL)")
            self.store._conn.execute("REINDEX")
            self.store._conn.execute("ANALYZE")
            self.store._conn.commit()
            self.store._conn.execute("VACUUM")
            self.store._conn.commit()
        after = self.database_health()
        return {
            "status": "repaired" if after["ok"] else "needs_recovery",
            "before": before,
            "after": after,
            "backup": backup,
        }

    def recovery_export(self) -> dict[str, Any]:
        """Create a JSON recovery bundle without replacing the live database."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.backup_dir / f"lumi-recovery-{stamp}.json"
        payload = {
            "created_at": utc_now(),
            "tasks": [
                task.to_dict(public=False)
                for task in self.store.list_tasks(limit=5000)
            ],
            "queues": self.store.list_queues(),
            "settings": {},
        }
        with self.store._lock:
            rows = self.store._conn.execute(
                "SELECT key, value_json FROM settings"
            ).fetchall()
        for row in rows:
            try:
                payload["settings"][row["key"]] = json.loads(row["value_json"])
            except Exception:
                payload["settings"][row["key"]] = None
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return {
            "path": str(target),
            "bytes": target.stat().st_size,
            "created_at": payload["created_at"],
        }

    def scan_missing_files(self, *, mark: bool = True) -> dict[str, Any]:
        missing: list[dict[str, Any]] = []
        scanned = 0
        for task in self.store.list_tasks(limit=5000):
            if task.status != TaskStatus.COMPLETED.value:
                continue
            scanned += 1
            value = str(task.final_path or "").strip()
            if not value:
                continue
            path = Path(value)
            exists = path.exists()
            if exists:
                if mark and task.metadata.pop("file_missing", None) is not None:
                    task.metadata.pop("completion_warning", None)
                    self.store.save_task(task)
                continue
            missing.append(
                {
                    "id": task.id,
                    "filename": task.filename,
                    "path": value,
                }
            )
            if mark:
                task.metadata["file_missing"] = True
                task.metadata["completion_warning"] = (
                    "The completed file is no longer at its recorded location."
                )
                self.store.save_task(task)
                self.store.append_event(
                    task.id,
                    "completed_file_missing",
                    {"path": value},
                )
        return {
            "scanned": scanned,
            "missing_count": len(missing),
            "missing": missing,
        }

    def storage_health(self) -> dict[str, Any]:
        directories: set[Path] = {self.store.data_dir}
        for task in self.store.list_tasks(limit=5000):
            for value in (task.target_dir, task.temp_dir):
                if value:
                    directories.add(Path(value))
        rows = []
        for directory in sorted(directories, key=lambda item: str(item).lower()):
            try:
                directory.mkdir(parents=True, exist_ok=True)
                usage = shutil.disk_usage(directory)
                writable = os.access(directory, os.W_OK)
                rows.append(
                    {
                        "path": str(directory),
                        "total_bytes": usage.total,
                        "used_bytes": usage.used,
                        "free_bytes": usage.free,
                        "writable": writable,
                        "ok": writable and usage.free > 256 * 1024 * 1024,
                    }
                )
            except OSError as exc:
                rows.append(
                    {
                        "path": str(directory),
                        "total_bytes": 0,
                        "used_bytes": 0,
                        "free_bytes": 0,
                        "writable": False,
                        "ok": False,
                        "error": str(exc),
                    }
                )
        return {
            "ok": all(item["ok"] for item in rows),
            "directories": rows,
        }

    def cleanup_backups(self, keep: int = 20) -> dict[str, int]:
        files = sorted(
            self.backup_dir.glob("lumi-*"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        removed = 0
        for path in files[max(1, int(keep)) :]:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return {"kept": min(len(files), max(1, int(keep))), "removed": removed}
