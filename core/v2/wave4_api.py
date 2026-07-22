"""Authenticated Wave 4 product, diagnostics and task-inspection APIs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request, send_file

from .inspector import TaskInspector
from .maintenance import MaintenanceService
from .runtime import _require_runtime


wave4_blueprint = Blueprint("lumi_wave4", __name__)


def _body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _call(operation: Callable[[], Any]):
    try:
        return jsonify(operation())
    except KeyError as exc:
        return jsonify({"error": f"not found: {exc}"}), 404
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except FileExistsError as exc:
        return jsonify({"error": str(exc)}), 409
    except (ValueError, IsADirectoryError) as exc:
        return jsonify({"error": str(exc)}), 400
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _maintenance() -> MaintenanceService:
    return MaintenanceService(_require_runtime().store)


def _inspector() -> TaskInspector:
    return TaskInspector(_require_runtime())


@wave4_blueprint.get("/api/maintenance/health")
def api_maintenance_health():
    return _call(lambda: _maintenance().health())


@wave4_blueprint.get("/api/maintenance/backups")
def api_list_backups():
    return _call(lambda: {"backups": _maintenance().list_backups()})


@wave4_blueprint.post("/api/maintenance/backups")
def api_create_backup():
    data = _body()
    return _call(
        lambda: _maintenance().create_backup(
            label=str(data.get("label") or "manual")
        )
    )


@wave4_blueprint.post("/api/maintenance/backups/<filename>/verify")
def api_verify_backup(filename: str):
    return _call(lambda: _maintenance().verify_backup(filename))


@wave4_blueprint.get("/api/maintenance/backups/<filename>/download")
def api_download_backup(filename: str):
    try:
        path = _maintenance()._backup_path(filename)
        return send_file(
            path,
            as_attachment=True,
            download_name=path.name,
            mimetype="application/zip",
        )
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404


@wave4_blueprint.post("/api/maintenance/repair")
def api_maintenance_repair():
    return _call(lambda: _maintenance().repair())


@wave4_blueprint.post("/api/maintenance/diagnostics")
def api_export_diagnostics():
    return _call(lambda: _maintenance().export_diagnostics())


@wave4_blueprint.get("/api/maintenance/diagnostics/<filename>/download")
def api_download_diagnostics(filename: str):
    safe = Path(filename).name
    root = _maintenance().diagnostics_dir.resolve()
    path = (root / safe).resolve()
    if path.parent != root or not path.is_file():
        return jsonify({"error": "diagnostic export not found"}), 404
    return send_file(
        path,
        as_attachment=True,
        download_name=path.name,
        mimetype="application/zip",
    )


@wave4_blueprint.get("/api/downloads/<task_id>/inspect")
def api_inspect_task(task_id: str):
    return _call(lambda: _inspector().details(task_id))


@wave4_blueprint.post("/api/downloads/<task_id>/restart")
def api_restart_task(task_id: str):
    data = _body()
    return _call(
        lambda: _inspector().restart(
            task_id,
            delete_existing=bool(data.get("delete_existing")),
        )
    )


@wave4_blueprint.post("/api/downloads/<task_id>/move")
def api_move_task_file(task_id: str):
    data = _body()
    destination = str(data.get("destination") or "").strip()
    if not destination:
        return jsonify({"error": "destination required"}), 400
    return _call(
        lambda: _inspector().move_or_rename(
            task_id,
            destination,
            overwrite=bool(data.get("overwrite")),
        )
    )


@wave4_blueprint.post("/api/downloads/<task_id>/locate")
def api_locate_task_file(task_id: str):
    path = str(_body().get("path") or "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    return _call(lambda: _inspector().locate(task_id, path))


@wave4_blueprint.patch("/api/downloads/<task_id>/placement")
def api_update_task_placement(task_id: str):
    data = _body()
    return _call(
        lambda: _inspector().update_placement(
            task_id,
            queue_id=(
                None if "queue_id" not in data else str(data.get("queue_id") or "")
            ),
            category_id=(
                None
                if "category_id" not in data
                else str(data.get("category_id") or "")
            ),
            priority=(
                None if "priority" not in data else int(data.get("priority") or 0)
            ),
        )
    )


@wave4_blueprint.delete("/api/downloads/<task_id>/product")
def api_delete_task_product(task_id: str):
    return _call(
        lambda: _inspector().delete(
            task_id,
            delete_file=bool(request.args.get("delete_file") in {"1", "true", "yes"}),
        )
    )
