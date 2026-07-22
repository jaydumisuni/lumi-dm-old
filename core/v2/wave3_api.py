"""Flask blueprint for Lumi Wave 3 media, torrent and archive APIs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from .wave3 import (
    archive_group,
    archive_info,
    archive_test,
    cancel_post_process,
    media_info,
    run_post_process,
    set_post_process_plan,
    torrent_info,
    wave3_capabilities,
)


wave3_blueprint = Blueprint("lumi_wave3", __name__)


def _body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _call(operation: Callable[[], Any]):
    try:
        return jsonify(operation())
    except KeyError as exc:
        return jsonify({"error": f"not found: {exc}"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@wave3_blueprint.get("/api/wave3/capabilities")
def api_wave3_capabilities():
    return jsonify(wave3_capabilities())


@wave3_blueprint.post("/api/media/resolve")
def api_media_resolve():
    data = _body()
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    return _call(
        lambda: media_info(url, playlist=bool(data.get("playlist", True)))
    )


@wave3_blueprint.post("/api/torrents/inspect")
def api_torrent_inspect():
    source = str(_body().get("source") or "").strip()
    if not source:
        return jsonify({"error": "source required"}), 400
    return _call(lambda: torrent_info(source))


@wave3_blueprint.post("/api/archives/group")
def api_archive_group():
    path = str(_body().get("path") or "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    return _call(lambda: archive_group(path))


@wave3_blueprint.post("/api/archives/list")
def api_archive_list():
    data = _body()
    path = str(data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    return _call(
        lambda: archive_info(path, password=str(data.get("password") or ""))
    )


@wave3_blueprint.post("/api/archives/test")
def api_archive_test():
    data = _body()
    path = str(data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    return _call(
        lambda: archive_test(path, password=str(data.get("password") or ""))
    )


@wave3_blueprint.put("/api/downloads/<task_id>/post-process")
def api_set_post_process(task_id: str):
    data = _body()
    plan = data.get("plan")
    if not isinstance(plan, dict):
        return jsonify({"error": "plan object required"}), 400
    password = data.get("archive_password")
    return _call(
        lambda: set_post_process_plan(
            task_id,
            plan,
            archive_password=(
                None if password is None else str(password)
            ),
        )
    )


@wave3_blueprint.post("/api/downloads/<task_id>/post-process/run")
def api_run_post_process(task_id: str):
    return _call(lambda: run_post_process(task_id))


@wave3_blueprint.post("/api/downloads/<task_id>/post-process/cancel")
def api_cancel_post_process(task_id: str):
    cancelled = cancel_post_process(task_id)
    return jsonify({"cancelled": cancelled, "id": task_id})
