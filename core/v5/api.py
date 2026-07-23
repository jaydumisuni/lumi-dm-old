"""Lumi V5 finishing API: firmware catalogue, branding and safe browser handoff."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from flask import Blueprint, Flask, jsonify, request, send_file

from core.v2.categories import CategoryRule
from core.v2.models import TaskStatus, TaskType, utc_now
from core.v2.wave2 import services as wave2_services

from .firmware import brands, list_devices, providers, search_firmware


wave5_api = Blueprint("lumi_wave5", __name__, url_prefix="/api/v5")


@dataclass(slots=True)
class V5Services:
    runtime: Any
    handoffs: "BrowserHandoffService"
    background_path: Path | None


_SERVICES: V5Services | None = None


def _services() -> V5Services:
    if _SERVICES is None:
        raise RuntimeError("Lumi V5 services are not configured")
    return _SERVICES


def _json_body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _error(exc: Exception):
    if isinstance(exc, KeyError):
        return jsonify({"error": str(exc).strip("'")}), 404
    if isinstance(exc, (ValueError, FileExistsError)):
        return jsonify({"error": str(exc)}), 400
    return jsonify({"error": str(exc)}), 500


class BrowserHandoffService:
    SETTINGS_KEY = "browser.handoffs.v1"

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self._lock = threading.RLock()

    def _load(self) -> dict[str, dict[str, Any]]:
        value = self.runtime.store.get_setting(self.SETTINGS_KEY, {})
        return dict(value) if isinstance(value, dict) else {}

    def _save(self, values: dict[str, dict[str, Any]]) -> None:
        self.runtime.store.set_setting(self.SETTINGS_KEY, values)

    def _cleanup(self, values: dict[str, dict[str, Any]]) -> bool:
        now = time.time()
        changed = False
        for key in list(values):
            item = values[key]
            if float(item.get("expires_at") or 0) <= now:
                if item.get("decision") == "pending":
                    item["decision"] = "browser"
                    item["reason"] = "Lumi confirmation timed out"
                    item["updated_at"] = utc_now()
                if now - float(item.get("expires_at") or 0) > 24 * 60 * 60:
                    values.pop(key, None)
                changed = True
        return changed

    def create(self, *, task_id: str, browser_download_id: int | str, original_url: str) -> dict[str, Any]:
        handoff_id = uuid.uuid4().hex
        item = {
            "id": handoff_id,
            "task_id": task_id,
            "browser_download_id": str(browser_download_id),
            "original_url": original_url,
            "decision": "pending",
            "reason": "",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "expires_at": time.time() + 10 * 60,
        }
        with self._lock:
            values = self._load()
            self._cleanup(values)
            values[handoff_id] = item
            self._save(values)
        return dict(item)

    def get(self, handoff_id: str) -> dict[str, Any]:
        with self._lock:
            values = self._load()
            changed = self._cleanup(values)
            item = values.get(handoff_id)
            if changed:
                self._save(values)
        if item is None:
            raise KeyError("browser handoff not found")
        task = self.runtime.get_task(str(item.get("task_id") or ""))
        return {
            **dict(item),
            "task": task.to_dict(public=True) if task else None,
        }

    def decide(self, handoff_id: str, decision: str, reason: str = "") -> dict[str, Any]:
        if decision not in {"lumi", "browser", "cancel"}:
            raise ValueError("decision must be lumi, browser, or cancel")
        with self._lock:
            values = self._load()
            item = values.get(handoff_id)
            if item is None:
                raise KeyError("browser handoff not found")
            item["decision"] = decision
            item["reason"] = reason
            item["updated_at"] = utc_now()
            values[handoff_id] = item
            self._save(values)
        return dict(item)

    def find_by_task(self, task_id: str) -> tuple[str, dict[str, Any]] | tuple[None, None]:
        with self._lock:
            values = self._load()
            self._cleanup(values)
            for key, item in values.items():
                if str(item.get("task_id") or "") == task_id and item.get("decision") == "pending":
                    return key, dict(item)
        return None, None


def _ensure_firmware_category() -> None:
    manager = wave2_services().categories
    if any(item.id == "firmware" for item in manager.list()):
        return
    manager.save(CategoryRule(
        id="firmware",
        name="Firmware",
        extensions=["ipsw", "zip", "tgz", "tar", "img", "bin", "pac", "ofp", "ozip", "kdz", "tot", "ftf"],
        domains=[],
        folder="Firmware",
        temp_folder="Firmware",
        auto_extract=False,
    ))


def _background_path() -> Path | None:
    candidates: list[Path] = []
    configured = os.environ.get("LUMIDM_BRANDING_DIR", "").strip()
    if configured:
        candidates.extend([
            Path(configured) / "backgroud .PNG",
            Path(configured) / "background.png",
            Path(configured) / "background.PNG",
        ])
    root = Path(__file__).resolve().parents[2]
    candidates.extend([
        root / "Resouces" / "backgroud .PNG",
        root / "Resources" / "background.png",
        root / "static" / "background.png",
    ])
    return next((path for path in candidates if path.is_file()), None)


@wave5_api.get("/branding/background")
def branding_background():
    path = _services().background_path
    if path is None or not path.is_file():
        return jsonify({"error": "background image unavailable"}), 404
    response = send_file(path, mimetype="image/png", conditional=True)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@wave5_api.get("/promotions/current")
def current_promotion():
    # Reserved for Hunter-managed TTG announcements. Disabled by default so the
    # product remains quiet until a signed announcement source is configured.
    return jsonify({"promotion": None})


@wave5_api.get("/firmware/catalogue")
def firmware_catalogue():
    return jsonify({
        "brands": brands(),
        "providers": providers(),
        "groups": ["Official OS", "Custom OS", "Community mirrors", "Community knowledge"],
        "warning": "Always verify the exact model, region, bootloader and rollback requirements before flashing.",
    })


@wave5_api.get("/firmware/devices")
def firmware_devices():
    return jsonify({"devices": list_devices(
        provider=str(request.args.get("provider") or ""),
        query=str(request.args.get("query") or ""),
        brand=str(request.args.get("brand") or ""),
    )})


@wave5_api.get("/firmware/search")
def firmware_search():
    return jsonify({"results": search_firmware(
        provider=str(request.args.get("provider") or "all"),
        brand=str(request.args.get("brand") or ""),
        device=str(request.args.get("device") or ""),
        query=str(request.args.get("query") or ""),
        channel=str(request.args.get("channel") or "all"),
        include_community=str(request.args.get("include_community") or "true").lower() not in {"0", "false", "no"},
    )})


@wave5_api.post("/firmware/stage")
def firmware_stage():
    data = _json_body()
    url = str(data.get("url") or "").strip()
    if not url.startswith(("http://", "https://", "ftp://")):
        return jsonify({"error": "a direct firmware URL is required"}), 400
    try:
        _ensure_firmware_category()
        active = wave2_services()
        result = active.start_http(
            url,
            target_dir=Path(str(data.get("target_dir") or active.runtime.store.get_setting("firmware.default_dir", "") or Path.home() / "Downloads")),
            temp_dir=Path(str(data.get("temp_dir") or active.runtime.store.get_setting("firmware.temp_dir", "") or active.runtime.data_dir / "temporary")),
            filename=str(data.get("filename") or "").strip(),
            connections=int(data.get("connections") or 0),
            queue_id=str(data.get("queue_id") or "default"),
            priority=int(data.get("priority") or 0),
            start_paused=True,
            duplicate_policy=str(data.get("duplicate_policy") or "reuse"),
            category_id="firmware",
        )
        task = active.runtime.get_task(result["id"])
        if task is None:
            raise RuntimeError("firmware task was not created")
        task.status = TaskStatus.STAGED.value
        task.category_id = "firmware"
        task.metadata.update({
            "firmware": True,
            "firmware_provider": str(data.get("provider") or ""),
            "firmware_source": str(data.get("source_name") or ""),
            "firmware_brand": str(data.get("brand") or ""),
            "firmware_device": str(data.get("device") or ""),
            "firmware_version": str(data.get("version") or ""),
            "firmware_build": str(data.get("build") or ""),
            "firmware_channel": str(data.get("channel") or ""),
            "firmware_sha256": str(data.get("sha256") or ""),
            "firmware_source_url": str(data.get("source_url") or url),
        })
        active.runtime.store.save_task(task)
        active.runtime.store.append_event(task.id, "firmware_staged", {
            "provider": task.metadata.get("firmware_provider"),
            "device": task.metadata.get("firmware_device"),
            "source_url": task.metadata.get("firmware_source_url"),
        })
        return jsonify(task.to_dict(public=True))
    except Exception as exc:
        return _error(exc)


@wave5_api.post("/browser/stage")
def browser_stage():
    data = _json_body()
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    try:
        active = wave2_services()
        target = Path(str(data.get("target_dir") or Path.home() / "Downloads"))
        dtype = str(data.get("type") or "auto")
        if dtype in {"torrent", "video"} or url.startswith("magnet:"):
            task = active.runtime.stage(
                url,
                target_dir=target,
                filename=str(data.get("filename") or ""),
                download_type="torrent" if dtype == "torrent" or url.startswith("magnet:") else "video",
            )
        else:
            result = active.start_http(
                url,
                target_dir=target,
                temp_dir=Path(str(data.get("temp_dir") or active.runtime.data_dir / "temporary")),
                filename=str(data.get("filename") or ""),
                connections=int(data.get("connections") or 0),
                start_paused=True,
                request_envelope=data.get("request_envelope"),
                duplicate_policy="rename",
                category_id=str(data.get("category_id") or ""),
            )
            task = active.runtime.get_task(result["id"])
            if task is None:
                raise RuntimeError("staged browser task was not created")
            task.status = TaskStatus.STAGED.value
            active.runtime.store.save_task(task)
        handoff = _services().handoffs.create(
            task_id=task.id,
            browser_download_id=data.get("browser_download_id", ""),
            original_url=url,
        )
        task.metadata["browser_handoff_id"] = handoff["id"]
        active.runtime.store.save_task(task)
        active.runtime.store.append_event(task.id, "browser_download_staged", {"handoff_id": handoff["id"]})
        return jsonify({"task": task.to_dict(public=True), "handoff": handoff})
    except Exception as exc:
        return _error(exc)


@wave5_api.get("/browser/handoffs/<handoff_id>")
def browser_handoff_status(handoff_id: str):
    try:
        return jsonify(_services().handoffs.get(handoff_id))
    except Exception as exc:
        return _error(exc)


@wave5_api.post("/browser/handoffs/<handoff_id>/decision")
def browser_handoff_decision(handoff_id: str):
    data = _json_body()
    try:
        return jsonify(_services().handoffs.decide(
            handoff_id,
            str(data.get("decision") or ""),
            str(data.get("reason") or ""),
        ))
    except Exception as exc:
        return _error(exc)


def install_v5(app: Flask) -> V5Services:
    global _SERVICES
    existing = app.extensions.get("lumi_v5")
    if isinstance(existing, V5Services):
        return existing
    runtime = wave2_services().runtime
    services = V5Services(
        runtime=runtime,
        handoffs=BrowserHandoffService(runtime),
        background_path=_background_path(),
    )
    _SERVICES = services
    if "lumi_wave5" not in app.blueprints:
        app.register_blueprint(wave5_api)
    app.extensions["lumi_v5"] = services
    try:
        _ensure_firmware_category()
    except Exception:
        pass
    return services
