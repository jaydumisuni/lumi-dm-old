"""Computer operating-system catalogue API for Lumi technicians."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from core.v2.categories import CategoryRule
from core.v2.models import TaskStatus
from core.v2.wave2 import services as wave2_services

from .os_catalog import catalogue, resolve_windows_iso, search_os


wave5_os_api = Blueprint("lumi_wave5_os", __name__, url_prefix="/api/v5/os")


def _body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _error(exc: Exception):
    if isinstance(exc, KeyError):
        return jsonify({"error": str(exc).strip("'")}), 404
    if isinstance(exc, (ValueError, FileExistsError)):
        return jsonify({"error": str(exc)}), 400
    return jsonify({"error": str(exc)}), 500


def _ensure_os_category() -> None:
    manager = wave2_services().categories
    if any(item.id == "operating-systems" for item in manager.list()):
        return
    manager.save(CategoryRule(
        id="operating-systems",
        name="Operating Systems",
        extensions=["iso", "img", "ipsw", "pkg", "dmg", "appimage", "vhd", "vhdx", "wim", "esd"],
        domains=[],
        folder="Operating Systems",
        temp_folder="Operating Systems",
        auto_extract=False,
    ))


@wave5_os_api.get("/catalogue")
def os_catalogue():
    return jsonify(catalogue())


@wave5_os_api.get("/search")
def os_search():
    try:
        return jsonify({"results": search_os(
            family=str(request.args.get("family") or ""),
            distribution=str(request.args.get("distribution") or ""),
            version=str(request.args.get("version") or ""),
            edition=str(request.args.get("edition") or ""),
            architecture=str(request.args.get("architecture") or ""),
            channel=str(request.args.get("channel") or "all"),
            language=str(request.args.get("language") or ""),
            query=str(request.args.get("query") or ""),
        )})
    except Exception as exc:
        return _error(exc)


@wave5_os_api.post("/windows/resolve")
def windows_resolve():
    data = _body()
    try:
        return jsonify({"result": resolve_windows_iso(
            version=str(data.get("version") or "Windows 11"),
            edition=str(data.get("edition") or "Home/Pro"),
            language=str(data.get("language") or "English International"),
            architecture=str(data.get("architecture") or "x64"),
        )})
    except Exception as exc:
        return _error(exc)


@wave5_os_api.post("/stage")
def os_stage():
    data = _body()
    url = str(data.get("url") or "").strip()
    if not url.startswith(("http://", "https://", "ftp://")):
        return jsonify({"error": "a direct operating-system image URL is required"}), 400
    try:
        _ensure_os_category()
        active = wave2_services()
        default_root = active.runtime.store.get_setting("os.default_dir", "") or Path.home() / "Downloads"
        result = active.start_http(
            url,
            target_dir=Path(str(data.get("target_dir") or default_root)),
            temp_dir=Path(str(data.get("temp_dir") or active.runtime.data_dir / "temporary")),
            filename=str(data.get("filename") or "").strip(),
            connections=int(data.get("connections") or 0),
            queue_id=str(data.get("queue_id") or "default"),
            priority=int(data.get("priority") or 0),
            start_paused=True,
            duplicate_policy=str(data.get("duplicate_policy") or "reuse"),
            category_id="operating-systems",
        )
        task = active.runtime.get_task(result["id"])
        if task is None:
            raise RuntimeError("operating-system task was not created")
        task.status = TaskStatus.STAGED.value
        task.category_id = "operating-systems"
        task.metadata.update({
            "operating_system": True,
            "os_family": str(data.get("family") or ""),
            "os_distribution": str(data.get("distribution") or ""),
            "os_version": str(data.get("version") or ""),
            "os_edition": str(data.get("edition") or ""),
            "os_architecture": str(data.get("architecture") or ""),
            "os_channel": str(data.get("channel") or ""),
            "os_provider": str(data.get("provider") or ""),
            "os_source": str(data.get("source_name") or ""),
            "os_source_url": str(data.get("source_url") or url),
            "os_sha256": str(data.get("sha256") or ""),
        })
        active.runtime.store.save_task(task)
        active.runtime.store.append_event(task.id, "operating_system_staged", {
            "family": task.metadata.get("os_family"),
            "version": task.metadata.get("os_version"),
            "source_url": task.metadata.get("os_source_url"),
        })
        return jsonify(task.to_dict(public=True))
    except Exception as exc:
        return _error(exc)


def install_os_api() -> None:
    try:
        _ensure_os_category()
    except Exception:
        pass
