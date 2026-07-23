"""Small persisted command bridge from paired clients to the Electron shell."""
from __future__ import annotations

import time
import uuid

from flask import Blueprint, current_app, jsonify, request

from core.v2.models import utc_now


wave5_desktop_api = Blueprint("lumi_wave5_desktop", __name__, url_prefix="/api/v5/desktop")
_KEY = "desktop.command.v1"
_ALLOWED = {"show-main", "show-widget"}


def _runtime():
    services = current_app.extensions.get("lumi_v5")
    if services is None:
        raise RuntimeError("Lumi V5 is unavailable")
    return services.runtime


@wave5_desktop_api.post("/command")
def create_command():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "")
    if action not in _ALLOWED:
        return jsonify({"error": "unsupported desktop command"}), 400
    command = {
        "id": uuid.uuid4().hex,
        "action": action,
        "created_at": utc_now(),
        "expires_at": time.time() + 30,
    }
    _runtime().store.set_setting(_KEY, command)
    return jsonify(command)


@wave5_desktop_api.get("/command")
def current_command():
    command = _runtime().store.get_setting(_KEY)
    if not isinstance(command, dict) or float(command.get("expires_at") or 0) <= time.time():
        return jsonify({"command": None})
    return jsonify({"command": command})


@wave5_desktop_api.post("/command/<command_id>/ack")
def acknowledge_command(command_id: str):
    command = _runtime().store.get_setting(_KEY)
    if isinstance(command, dict) and str(command.get("id") or "") == command_id:
        _runtime().store.set_setting(_KEY, None)
    return jsonify({"status": "acknowledged"})
