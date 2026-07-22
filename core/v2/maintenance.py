"""Database health, backup, repair and sanitized diagnostics for Lumi DM."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import tempfile
from typing import Any
import zipfile

from .models import TaskStatus, utc_now
from .store import StateStore
from .tools import capabilities as tool_capabilities


class MaintenanceError(RuntimeError):
    pass


class MaintenanceService:
    def __init__(self, store: StateStore):
        self.store = store
        self.data_dir = store.data_dir
        self.backup_dir = self.data_dir / "backups"
        self.diagnostics_dir = self.data_dir / "diagnostics"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.diagnostics_dir.mkdir(parents=True, exist_ok=True)

    def database_check(self) -> dict[str, Any]:
        with self.store._lock:
            integrity_rows = self.store._conn.execute("PRAGMA integrity_check").fetchall()
            quick_rows = self.store._conn.execute("PRAGMA quick_check").fetchall()
            page_count = int(self.store._conn.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(self.store._conn.execute("PRAGMA page_size").fetchone()[0])
            journal_mode = str(
                self.store._conn.execute("PRAGMA journal_mode").fetchone()[0]
            )
            schema_version = int(
                self.store._conn.execute("PRAGMA user_version").fetchone()[0]
            )
        integrity = [str(row[0]) for row in integrity_rows]
        quick = [str(row[0]) for row in quick_rows]
        return {
            "ok": integrity == ["ok"] and quick == ["ok"],
            "integrity": integrity,
            "quick_check": quick,
            "journal_mode": journal_mode,
            "schema_version": schema_version,
            "page_count": page_count,
            "page_size": page_size,
            "estimated_bytes": page_count * page_size,
            "database_path": str(self.store.db_path),
        }

    def filesystem_check(self) -> dict[str, Any]:
        usage = shutil.disk_usage(self.data_dir)
        missing: list[dict[str, str]] = []
        partials: list[dict[str, str]] = []
        for task in self.store.list_tasks(limit=5000):
            final = Path(task.final_path) if task.final_path else None
            partial = Path(task.partial_path) if task.partial_path else None
            if task.status == TaskStatus.COMPLETED.value and final and not final.is_file():
                missing.append(
                    {
                        "id": task.id,
                        "filename": task.filename,
                        "expected_path": str(final),
                    }
                )
            if partial and partial.is_file():
                partials.append(
                    {
                        "id": task.id,
                        "path": str(partial),
                        "bytes": str(partial.stat().st_size),
                    }
                )
        orphan_temporary = [
            str(path)
            for path in self.store.resume_dir.glob("*.tmp")
            if path.is_file()
        ]
        vault_dir = self.data_dir / "vault"
        vault = {
            "present": vault_dir.is_dir(),
            "key_present": (vault_dir / "vault.key").is_file(),
            "entries_present": (vault_dir / "entries.json").is_file(),
        }
        return {
            "data_dir": str(self.data_dir),
            "disk_total": usage.total,
            "disk_used": usage.used,
            "disk_free": usage.free,
            "missing_completed_files": missing,
            "partial_files": partials,
            "orphan_temporary_files": orphan_temporary,
            "vault": vault,
        }

    def health(self) -> dict[str, Any]:
        database = self.database_check()
        filesystem = self.filesystem_check()
        task_counts: dict[str, int] = {}
        for task in self.store.list_tasks(limit=5000):
            task_counts[task.status] = task_counts.get(task.status, 0) + 1
        return {
            "ok": database["ok"] and not filesystem["missing_completed_files"],
            "checked_at": utc_now(),
            "database": database,
            "filesystem": filesystem,
            "tasks": task_counts,
            "queues": len(self.store.list_queues()),
            "tools": tool_capabilities(),
            "runtime": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "machine": platform.machine(),
                "pid": os.getpid(),
            },
        }

    def create_backup(self, *, label: str = "manual") -> dict[str, Any]:
        safe_label = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in label.strip()[:60]
        ).strip("-") or "manual"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = self.backup_dir / f"lumi-{timestamp}-{safe_label}.zip"
        temporary_root = Path(tempfile.mkdtemp(prefix="lumi-backup-"))
        database_copy = temporary_root / "lumi.db"
        manifest = {
            "format": "lumi-backup-v2",
            "created_at": utc_now(),
            "label": safe_label,
            "schema_version": self.database_check()["schema_version"],
            "includes_encrypted_vault": (self.data_dir / "vault").is_dir(),
        }
        try:
            destination = sqlite3.connect(database_copy)
            try:
                with self.store._lock:
                    self.store._conn.backup(destination)
            finally:
                destination.close()
            (temporary_root / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            with zipfile.ZipFile(
                archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as output:
                output.write(database_copy, "lumi.db")
                output.write(temporary_root / "manifest.json", "manifest.json")
                self._add_directory(output, self.store.resume_dir, "resume")
                self._add_directory(output, self.data_dir / "vault", "vault")
                for name in ("settings.json", "downloads.json"):
                    candidate = self.data_dir / name
                    if candidate.is_file():
                        output.write(candidate, name)
            try:
                os.chmod(archive, 0o600)
            except OSError:
                pass
            return {
                "status": "created",
                "path": str(archive),
                "filename": archive.name,
                "bytes": archive.stat().st_size,
                "manifest": manifest,
            }
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    def list_backups(self) -> list[dict[str, Any]]:
        values = []
        for path in sorted(
            self.backup_dir.glob("lumi-*.zip"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            values.append(
                {
                    "filename": path.name,
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "modified_at": datetime.fromtimestamp(
                        path.stat().st_mtime,
                        tz=timezone.utc,
                    ).isoformat(timespec="seconds"),
                }
            )
        return values

    def verify_backup(self, filename: str) -> dict[str, Any]:
        path = self._backup_path(filename)
        try:
            with zipfile.ZipFile(path) as archive:
                bad = archive.testzip()
                names = set(archive.namelist())
                if bad:
                    raise MaintenanceError(f"Backup contains a damaged entry: {bad}")
                if not {"manifest.json", "lumi.db"} <= names:
                    raise MaintenanceError("Backup is missing manifest.json or lumi.db")
                manifest = json.loads(archive.read("manifest.json"))
                with tempfile.TemporaryDirectory(prefix="lumi-verify-") as temporary:
                    database = Path(temporary) / "lumi.db"
                    database.write_bytes(archive.read("lumi.db"))
                    connection = sqlite3.connect(database)
                    try:
                        integrity = [
                            str(row[0])
                            for row in connection.execute("PRAGMA integrity_check")
                        ]
                    finally:
                        connection.close()
            return {
                "ok": integrity == ["ok"],
                "filename": path.name,
                "manifest": manifest,
                "integrity": integrity,
                "entries": len(names),
            }
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise MaintenanceError(f"Backup verification failed: {exc}") from exc

    def repair(self) -> dict[str, Any]:
        backup = self.create_backup(label="before-repair")
        actions: list[dict[str, Any]] = []
        with self.store._lock:
            checkpoint = self.store._conn.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
        actions.append({"action": "wal_checkpoint", "result": list(checkpoint or [])})

        recovered = self.store.recover_incomplete()
        actions.append({"action": "recover_incomplete", "tasks": recovered})

        for path in self.store.resume_dir.glob("*.tmp"):
            try:
                path.unlink()
                actions.append({"action": "remove_orphan", "path": str(path)})
            except OSError:
                continue

        missing = 0
        for task in self.store.list_tasks(limit=5000):
            if (
                task.status == TaskStatus.COMPLETED.value
                and task.final_path
                and not Path(task.final_path).is_file()
            ):
                task.status = TaskStatus.FAILED.value
                task.error = "Completed file is missing from disk"
                task.error_code = "missing_file"
                task.finished_at = ""
                self.store.save_task(task)
                self.store.append_event(
                    task.id,
                    "missing_file_detected",
                    {"path": task.final_path},
                )
                missing += 1
        actions.append({"action": "mark_missing_files", "tasks": missing})

        after = self.database_check()
        return {
            "status": "completed" if after["ok"] else "needs_attention",
            "backup": backup,
            "actions": actions,
            "database": after,
        }

    def export_diagnostics(self) -> dict[str, Any]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = self.diagnostics_dir / f"lumi-diagnostics-{timestamp}.zip"
        tasks = [task.to_dict(public=True) for task in self.store.list_tasks(limit=5000)]
        # Paths help diagnose local issues but home-directory prefixes are reduced.
        home = str(Path.home())
        payloads = {
            "health.json": self.health(),
            "tasks.json": tasks,
            "queues.json": self.store.list_queues(),
            "recent-events.json": {
                task["id"]: self.store.list_events(task["id"], 30)
                for task in tasks[-100:]
            },
        }
        with zipfile.ZipFile(
            archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as output:
            for name, payload in payloads.items():
                text = json.dumps(payload, indent=2, sort_keys=True, default=str)
                text = text.replace(home, "<HOME>")
                output.writestr(name, text)
        return {
            "status": "created",
            "path": str(archive),
            "filename": archive.name,
            "bytes": archive.stat().st_size,
        }

    def _backup_path(self, filename: str) -> Path:
        safe = Path(filename).name
        path = self.backup_dir / safe
        if not path.is_file() or path.parent.resolve() != self.backup_dir.resolve():
            raise FileNotFoundError(safe)
        return path

    @staticmethod
    def _add_directory(
        archive: zipfile.ZipFile,
        directory: Path,
        prefix: str,
    ) -> None:
        if not directory.is_dir():
            return
        for path in directory.rglob("*"):
            if path.is_file():
                archive.write(path, str(Path(prefix) / path.relative_to(directory)))
