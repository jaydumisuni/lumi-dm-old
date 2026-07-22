from __future__ import annotations

import json
from pathlib import Path
import zipfile

from core.v2.models import DownloadTask, RequestEnvelope, TaskStatus, TaskType
from core.v2.store import StateStore
from core.v4.diagnostics import DiagnosticsService
from core.v4.maintenance import MaintenanceService


def _completed_task(store: StateStore, tmp_path: Path, *, missing: bool) -> DownloadTask:
    output = tmp_path / "private-home" / "Downloads" / "proof.bin"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not missing:
        output.write_bytes(b"lumi-proof")
    task = DownloadTask(
        id="missing-task" if missing else "present-task",
        type=TaskType.HTTP.value,
        status=TaskStatus.COMPLETED.value,
        request=RequestEnvelope(
            url="https://user:password@example.invalid/file.bin?token=private",
            headers={
                "Authorization": "Bearer secret-value",
                "Cookie": "session=secret-cookie",
            },
        ),
        filename=output.name,
        target_dir=str(output.parent),
        temp_dir=str(tmp_path / "private-home" / "Temporary"),
        final_path=str(output),
        partial_path="",
        downloaded_bytes=10,
        total_bytes=10,
        progress_percent=100.0,
        metadata={"private_path": str(output), "api_token": "never-export"},
    )
    store.save_task(task)
    return task


def test_database_health_is_cached_for_polling_and_forced_for_maintenance(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "data")
    maintenance = MaintenanceService(store)
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)

    maintenance.database_health()
    maintenance.database_health()

    integrity_checks = [
        item for item in statements if "PRAGMA integrity_check" in item
    ]
    assert len(integrity_checks) == 1

    maintenance.database_health(force=True)
    integrity_checks = [
        item for item in statements if "PRAGMA integrity_check" in item
    ]
    assert len(integrity_checks) == 2
    store._conn.set_trace_callback(None)
    store.close()


def test_database_backup_repair_and_recovery_export_are_readable(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    _completed_task(store, tmp_path, missing=False)
    maintenance = MaintenanceService(store)

    before = maintenance.database_health()
    backup = maintenance.backup_database("pytest")
    repaired = maintenance.repair_database()
    recovery = maintenance.recovery_export()

    assert before["ok"]
    assert Path(backup["path"]).is_file()
    assert repaired["after"]["ok"]
    payload = json.loads(Path(recovery["path"]).read_text(encoding="utf-8"))
    assert payload["tasks"][0]["id"] == "present-task"
    store.close()


def test_missing_file_scan_marks_once_and_repairs_task_warning(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "data")
    task = _completed_task(store, tmp_path, missing=True)
    maintenance = MaintenanceService(store)

    result = maintenance.scan_missing_files(mark=True)
    maintenance.scan_missing_files(mark=True)

    assert result["missing_count"] == 1
    marked = store.get_task(task.id)
    assert marked is not None
    assert marked.metadata["file_missing"] is True
    assert "no longer" in marked.metadata["completion_warning"]
    events = [
        item
        for item in store.list_events(task.id)
        if item["event_type"] == "completed_file_missing"
    ]
    assert len(events) == 1

    Path(marked.final_path).write_bytes(b"restored")
    repaired = maintenance.scan_missing_files(mark=True)
    assert repaired["missing_count"] == 0
    current = store.get_task(task.id)
    assert current is not None
    assert "file_missing" not in current.metadata
    store.close()


def test_diagnostics_bundle_redacts_secrets_queries_and_private_paths(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "data")
    task = _completed_task(store, tmp_path, missing=False)
    task.error = (
        "Request failed with Bearer inline-secret at "
        "https://example.invalid/file?token=inline-private from "
        "C:\\Users\\John\\Downloads\\private.bin"
    )
    store.save_task(task)
    store.append_event(
        task.id,
        "provider_error",
        {
            "message": (
                "Cookie: raw-cookie; password=raw-password; "
                "path /home/john/private/report.txt"
            )
        },
    )
    maintenance = MaintenanceService(store)
    diagnostics = DiagnosticsService(store, maintenance)

    exported = diagnostics.export()

    archive = Path(exported["path"])
    assert archive.is_file()
    with zipfile.ZipFile(archive) as bundle:
        combined = "\n".join(
            bundle.read(name).decode("utf-8")
            for name in bundle.namelist()
        )
    for secret in (
        "secret-value",
        "secret-cookie",
        "never-export",
        "inline-secret",
        "inline-private",
        "raw-cookie",
        "raw-password",
        "C:\\Users\\John",
        "/home/john/private",
    ):
        assert secret not in combined
    assert str(tmp_path / "private-home") not in combined
    assert "token=private" not in combined
    assert "<private-path>" in combined
    assert "<redacted>" in combined
    store.close()
