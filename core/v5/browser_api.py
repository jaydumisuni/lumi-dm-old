"""Safe browser-download staging and decision API.

The browser keeps ownership of its download until Lumi has persisted a pending
request. The desktop setup popup then decides whether Lumi takes over, the browser
resumes, or both copies are cancelled.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from core.v2.models import RequestEnvelope, TaskStatus, TaskType
from core.v2.wave2 import services as wave2_services


wave5_browser_api = Blueprint("lumi_wave5_browser", __name__, url_prefix="/api/v5/browser")
_BROWSER_PENDING = "browser_pending"


def _body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _services():
    value = current_app.extensions.get("lumi_v5")
    if value is None:
        raise RuntimeError("Lumi V5 is not installed")
    return value


def _error(exc: Exception):
    if isinstance(exc, KeyError):
        return jsonify({"error": str(exc).strip("'")}), 404
    if isinstance(exc, (ValueError, FileExistsError)):
        return jsonify({"error": str(exc)}), 400
    return jsonify({"error": str(exc)}), 500


def _remove_pending_task(task_id: str) -> None:
    runtime = _services().runtime
    task = runtime.get_task(task_id)
    if task is None:
        return
    if task.status in {_BROWSER_PENDING, TaskStatus.STAGED.value, TaskStatus.PAUSED.value}:
        runtime.store.delete_task(task_id)


def _stage_task(data: dict[str, Any]):
    active = wave2_services()
    runtime = active.runtime
    url = str(data.get("url") or "").strip()
    if not url:
        raise ValueError("url required")
    target = Path(str(data.get("target_dir") or Path.home() / "Downloads"))
    target.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(data.get("temp_dir") or runtime.data_dir / "temporary"))
    temporary.mkdir(parents=True, exist_ok=True)
    dtype = str(data.get("type") or "auto").lower()
    envelope = RequestEnvelope.from_dict(data.get("request_envelope") or {"url": url})
    envelope.url = envelope.url or url

    if dtype == "torrent" or url.startswith("magnet:") or url.lower().endswith(".torrent"):
        task = runtime.create_delegated_task(
            TaskType.TORRENT.value,
            url,
            target_dir=target,
            metadata={
                "filename": str(data.get("filename") or "Torrent download"),
                "connections": int(data.get("connections") or 0),
                "browser_capture": True,
            },
            queue_id=str(data.get("queue_id") or "default"),
            priority=int(data.get("priority") or 0),
            start_paused=True,
        )
    elif dtype in {"video", "hls", "dash"}:
        task = runtime.create_delegated_task(
            TaskType.VIDEO.value,
            url,
            target_dir=target,
            metadata={
                "filename": str(data.get("filename") or "Media download"),
                "format_id": str(data.get("format_id") or "bestvideo+bestaudio/best"),
                "browser_capture": True,
                "detected_type": dtype,
            },
            queue_id=str(data.get("queue_id") or "default"),
            priority=int(data.get("priority") or 0),
            start_paused=True,
        )
    else:
        secured = active.capture({
            **(data.get("request_envelope") or {}),
            "url": envelope.url,
            "suggested_filename": str(data.get("filename") or envelope.suggested_filename or ""),
        })
        task = runtime.create_http_task(
            url,
            target_dir=target,
            temp_dir=temporary,
            filename=str(data.get("filename") or secured.suggested_filename or ""),
            resume=True,
            connections=int(data.get("connections") or 0),
            max_speed_bps=int(data.get("max_speed_bps") or 0),
            queue_id=str(data.get("queue_id") or "default"),
            priority=int(data.get("priority") or 0),
            start_paused=True,
            request_envelope=secured,
            duplicate_policy="rename",
        )

    task.status = _BROWSER_PENDING
    task.metadata.update({
        "browser_capture": True,
        "browser_download_id": str(data.get("browser_download_id") or ""),
        "browser_original_url": url,
        "browser_referrer": str(data.get("referrer") or envelope.original_page or ""),
    })
    runtime.store.save_task(task)
    return task


@wave5_browser_api.post("/capture")
def capture_download():
    data = _body()
    try:
        task = _stage_task(data)
        handoff = _services().handoffs.create(
            task_id=task.id,
            browser_download_id=data.get("browser_download_id", ""),
            original_url=str(data.get("url") or ""),
        )
        task.metadata["browser_handoff_id"] = handoff["id"]
        _services().runtime.store.save_task(task)
        _services().runtime.store.append_event(
            task.id,
            "browser_download_pending",
            {"handoff_id": handoff["id"]},
        )
        return jsonify({"task": task.to_dict(public=True), "handoff": handoff})
    except Exception as exc:
        return _error(exc)


@wave5_browser_api.get("/handoffs/<handoff_id>")
def handoff_status(handoff_id: str):
    try:
        result = _services().handoffs.get(handoff_id)
        if result.get("decision") == "browser":
            task = result.get("task") or {}
            if task.get("status") == _BROWSER_PENDING:
                _remove_pending_task(str(task.get("id") or ""))
                result["task"] = None
        return jsonify(result)
    except Exception as exc:
        return _error(exc)


@wave5_browser_api.post("/handoffs/<handoff_id>/confirm")
def confirm_handoff(handoff_id: str):
    data = _body()
    try:
        handoff = _services().handoffs.get(handoff_id)
        task_id = str(handoff.get("task_id") or "")
        runtime = _services().runtime
        task = runtime.get_task(task_id)
        if task is None:
            raise KeyError("pending download not found")
        if task.status != _BROWSER_PENDING:
            raise ValueError("download is no longer waiting for confirmation")

        filename = Path(str(data.get("filename") or task.filename or "download.bin")).name
        target_dir = Path(str(data.get("target_dir") or task.target_dir or Path.home() / "Downloads"))
        target_dir.mkdir(parents=True, exist_ok=True)
        task.filename = filename
        task.target_dir = str(target_dir)
        task.final_path = str(target_dir / filename)
        if task.type == TaskType.HTTP.value:
            task.partial_path = str(Path(task.temp_dir) / f"{filename}.part")
        queue_id = str(data.get("queue_id") or task.queue_id or "default")
        if runtime.store.get_queue(queue_id) is None:
            raise KeyError(f"unknown queue: {queue_id}")
        task.queue_id = queue_id
        category_id = str(data.get("category_id") or task.category_id or "other")
        task.category_id = category_id
        task.priority = int(data.get("priority") or task.priority or 0)
        if data.get("connections") not in (None, ""):
            task.connections = max(1, min(128, int(data.get("connections") or 1)))
        if data.get("max_speed_bps") not in (None, ""):
            task.max_speed_bps = max(0, int(data.get("max_speed_bps") or 0))
        task.duplicate_policy = str(data.get("duplicate_policy") or task.duplicate_policy or "rename")
        task.metadata["browser_confirmed"] = True
        task.metadata["start_mode"] = str(data.get("start_mode") or "now")
        task.status = (
            TaskStatus.PAUSED.value
            if str(data.get("start_mode") or "now") == "later"
            else TaskStatus.QUEUED.value
        )
        runtime.store.save_task(task)
        runtime.store.append_event(
            task.id,
            "browser_download_confirmed",
            {"start_mode": task.metadata["start_mode"], "handoff_id": handoff_id},
        )
        if task.status == TaskStatus.QUEUED.value:
            runtime.queue.wake()
        decision = _services().handoffs.decide(handoff_id, "lumi", "Confirmed in Lumi desktop popup")
        return jsonify({"task": task.to_dict(public=True), "handoff": decision})
    except Exception as exc:
        return _error(exc)


@wave5_browser_api.post("/handoffs/<handoff_id>/browser")
def use_browser(handoff_id: str):
    try:
        handoff = _services().handoffs.get(handoff_id)
        _remove_pending_task(str(handoff.get("task_id") or ""))
        return jsonify(_services().handoffs.decide(
            handoff_id,
            "browser",
            "User chose the browser download",
        ))
    except Exception as exc:
        return _error(exc)


@wave5_browser_api.post("/handoffs/<handoff_id>/cancel")
def cancel_handoff(handoff_id: str):
    try:
        handoff = _services().handoffs.get(handoff_id)
        _remove_pending_task(str(handoff.get("task_id") or ""))
        return jsonify(_services().handoffs.decide(
            handoff_id,
            "cancel",
            "User cancelled the download",
        ))
    except Exception as exc:
        return _error(exc)
