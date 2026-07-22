"""Authenticated final-product API for Lumi DM."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
from typing import Any, Callable

from flask import Blueprint, jsonify, make_response, request

from core.v2.models import TaskStatus
from core.v2.runtime import LumiRuntime
from core.v2.store import StateStore

from .diagnostics import DiagnosticsService
from .maintenance import MaintenanceService
from .security import (
    SecurityManager,
    auth_context,
    session_cookie_name,
)


wave4_api = Blueprint("lumi_wave4", __name__)


@dataclass(slots=True)
class V4Services:
    runtime: LumiRuntime
    security: SecurityManager
    maintenance: MaintenanceService
    diagnostics: DiagnosticsService


_SERVICES: V4Services | None = None


def configure_services(services: V4Services) -> None:
    global _SERVICES
    _SERVICES = services


def services() -> V4Services:
    if _SERVICES is None:
        raise RuntimeError("Lumi V4 services are not configured")
    return _SERVICES


def _body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _status(exc: Exception) -> int:
    if isinstance(exc, KeyError):
        return 404
    if isinstance(exc, FileNotFoundError):
        return 404
    if isinstance(exc, FileExistsError):
        return 409
    if isinstance(exc, PermissionError):
        return 403
    if isinstance(exc, (ValueError, RuntimeError)):
        return 400
    return 500


def _call(operation: Callable[[], Any]):
    try:
        return jsonify(operation())
    except Exception as exc:
        return jsonify({"error": str(exc)}), _status(exc)


def _task(task_id: str):
    task = services().runtime.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    return task


@wave4_api.get("/api/security/bootstrap")
def security_bootstrap():
    try:
        token, context = services().security.bootstrap(
            request.remote_addr or "",
            str(request.headers.get("User-Agent") or ""),
        )
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    response = make_response(
        jsonify(
            {
                "authenticated": True,
                "role": context.role,
                "client_name": context.client_name,
            }
        )
    )
    response.set_cookie(
        session_cookie_name(),
        token,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=False,
        samesite="Strict",
        path="/",
    )
    return response


@wave4_api.post("/api/security/pair")
def security_pair():
    data = _body()
    code = str(data.get("code") or "").strip()
    if not code:
        return jsonify({"error": "pairing code required"}), 400
    return _call(
        lambda: services().security.exchange_pairing_code(
            code=code,
            requested_name=str(data.get("client_name") or ""),
            remote_addr=request.remote_addr or "unknown",
        )
    )


@wave4_api.get("/api/v4/security/me")
def security_me():
    context = auth_context()
    return jsonify(
        {
            "authenticated": context is not None,
            "role": context.role if context else "",
            "client_name": context.client_name if context else "",
            "token_kind": context.token_kind if context else "",
            "can_write": bool(context and context.can_write),
        }
    )


@wave4_api.post("/api/v4/security/pairing")
def security_pairing():
    data = _body()
    return _call(
        lambda: services().security.create_pairing_code(
            role=str(data.get("role") or "read_only"),
            client_name=str(data.get("client_name") or "Paired client"),
            expires_in=int(data.get("expires_in") or 600),
        )
    )


@wave4_api.get("/api/v4/security/clients")
def security_clients():
    return jsonify({"clients": services().security.list_clients()})


@wave4_api.delete("/api/v4/security/clients/<token_id>")
def security_revoke(token_id: str):
    if not services().security.revoke(token_id):
        return jsonify({"error": "paired client not found"}), 404
    return jsonify({"status": "revoked", "id": token_id})


@wave4_api.get("/api/v4/overview")
def overview():
    runtime = services().runtime
    tasks = runtime.list_tasks(5000)
    counts: dict[str, int] = {}
    total_speed = 0.0
    total_downloaded = 0
    total_size = 0
    warnings = 0
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
        total_speed += float(task.speed_bytes_per_sec or 0)
        total_downloaded += int(task.downloaded_bytes or 0)
        total_size += int(task.total_bytes or 0)
        if task.metadata.get("completion_warning") or task.post_process.get("warning"):
            warnings += 1
    return jsonify(
        {
            "counts": counts,
            "total_tasks": len(tasks),
            "total_speed_bytes_per_sec": total_speed,
            "downloaded_bytes": total_downloaded,
            "known_total_bytes": total_size,
            "warnings": warnings,
            "queues": runtime.store.list_queues(),
            "database": services().maintenance.database_health(),
        }
    )


@wave4_api.get("/api/v4/tasks/<task_id>/inspector")
def task_inspector(task_id: str):
    task = _task(task_id)
    resume = services().runtime.store.load_resume(task_id) or {}
    queue = services().runtime.store.get_queue(task.queue_id)
    metadata_files = task.metadata.get("files") or task.metadata.get("output_files") or []
    files: list[dict[str, Any]] = []
    for index, item in enumerate(metadata_files):
        if isinstance(item, dict):
            files.append(dict(item))
        else:
            value = str(item)
            files.append(
                {
                    "index": index,
                    "path": value,
                    "name": Path(value).name,
                    "exists": Path(value).exists(),
                }
            )
    return jsonify(
        {
            "task": task.to_dict(public=True),
            "overview": {
                "warning": (
                    task.metadata.get("completion_warning")
                    or task.post_process.get("warning")
                    or ""
                ),
                "file_exists": bool(task.final_path and Path(task.final_path).exists()),
            },
            "connections": resume.get("segments") or [],
            "request": task.request.redacted_dict(),
            "queue": queue,
            "files": files,
            "post_processing": task.post_process,
            "events": services().runtime.store.list_events(task_id, limit=300),
        }
    )


def _safe_filename(value: str) -> str:
    filename = Path(value.strip()).name
    if not filename or filename in {".", ".."}:
        raise ValueError("A valid filename is required")
    return filename


@wave4_api.post("/api/v4/tasks/<task_id>/move")
def task_move(task_id: str):
    data = _body()

    def operation():
        task = _task(task_id)
        if task.status not in {
            TaskStatus.COMPLETED.value,
            TaskStatus.PAUSED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        }:
            raise RuntimeError("Pause or finish the task before moving its file")
        source = Path(task.final_path)
        if not source.exists():
            raise FileNotFoundError(source)
        target_dir = Path(str(data.get("target_dir") or source.parent)).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = _safe_filename(str(data.get("filename") or source.name))
        destination = target_dir / filename
        if destination.exists() and destination.resolve() != source.resolve():
            raise FileExistsError(destination)
        if source.resolve() != destination.resolve():
            try:
                os.replace(source, destination)
            except OSError:
                shutil.move(str(source), str(destination))
        task.final_path = str(destination)
        task.target_dir = str(target_dir)
        task.filename = destination.name
        task.metadata.pop("file_missing", None)
        task.metadata.pop("completion_warning", None)
        services().runtime.store.save_task(task)
        services().runtime.store.append_event(
            task.id,
            "file_moved",
            {"filename": destination.name},
        )
        return task.to_dict(public=True)

    return _call(operation)


@wave4_api.post("/api/v4/tasks/<task_id>/locate")
def task_locate(task_id: str):
    data = _body()

    def operation():
        task = _task(task_id)
        path = Path(str(data.get("path") or "")).expanduser()
        if not path.is_file() and not path.is_dir():
            raise FileNotFoundError(path)
        task.final_path = str(path)
        task.filename = path.name
        task.target_dir = str(path.parent if path.is_file() else path)
        task.metadata.pop("file_missing", None)
        task.metadata.pop("completion_warning", None)
        services().runtime.store.save_task(task)
        services().runtime.store.append_event(
            task.id,
            "file_location_repaired",
            {"filename": path.name},
        )
        return task.to_dict(public=True)

    return _call(operation)


@wave4_api.get("/api/v4/diagnostics")
def diagnostics_summary():
    return jsonify(services().diagnostics.summary())


@wave4_api.post("/api/v4/diagnostics/export")
def diagnostics_export():
    return _call(services().diagnostics.export)


@wave4_api.get("/api/v4/maintenance/database")
def database_health():
    return jsonify(services().maintenance.database_health())


@wave4_api.post("/api/v4/maintenance/database/backup")
def database_backup():
    return _call(
        lambda: services().maintenance.backup_database(
            str(_body().get("label") or "manual")
        )
    )


@wave4_api.post("/api/v4/maintenance/database/repair")
def database_repair():
    return _call(services().maintenance.repair_database)


@wave4_api.post("/api/v4/maintenance/database/recovery-export")
def database_recovery_export():
    return _call(services().maintenance.recovery_export)


@wave4_api.get("/api/v4/maintenance/storage")
def storage_health():
    return jsonify(services().maintenance.storage_health())


@wave4_api.post("/api/v4/maintenance/missing-files")
def missing_files():
    data = _body()
    return jsonify(
        services().maintenance.scan_missing_files(
            mark=bool(data.get("mark", True))
        )
    )


@wave4_api.post("/api/v4/maintenance/backups/cleanup")
def cleanup_backups():
    return _call(
        lambda: services().maintenance.cleanup_backups(
            int(_body().get("keep") or 20)
        )
    )
