from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import time

from core.v2.models import DownloadTask, RequestEnvelope
from core.v2.store import Store
from core.v5.browser_handoff import BrowserHandoffService


ROOT = Path(__file__).resolve().parents[1]


def _background_path() -> Path | None:
    resources = ROOT / "Resouces"
    for candidate in resources.glob("*"):
        if candidate.is_file() and candidate.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            return candidate
    return None


def _task(task_id: str = "task-1") -> DownloadTask:
    return DownloadTask(
        id=task_id,
        url="https://example.invalid/file.zip",
        filename="file.zip",
        target_dir="/tmp",
        temp_dir="/tmp",
        request=RequestEnvelope(url="https://example.invalid/file.zip"),
    )


def test_browser_handoff_persists_and_decides(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    store.upsert_task(_task())
    service = BrowserHandoffService(store, tmp_path / "handoffs.json")
    handoff = service.create(
        task_id="task-1",
        browser_download_id=42,
        original_url="https://example.invalid/file.zip",
    )
    assert service.get(handoff["id"])["decision"] == "pending"
    service.decide(handoff["id"], "lumi", reason="accepted")
    decided = service.get(handoff["id"])
    assert decided["decision"] == "lumi"
    assert decided["reason"] == "accepted"
    store.close()


def test_browser_handoff_expiry_returns_to_browser(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    store.upsert_task(_task("timeout-task"))
    service = BrowserHandoffService(store, tmp_path / "handoffs.json")
    second = service.create(
        task_id="timeout-task",
        browser_download_id=43,
        original_url="https://example.invalid/second.zip",
    )
    values = service._load()
    values[second["id"]]["expires_at"] = time.time() - 1
    service._save(values)

    expired = service.get(second["id"])
    assert expired["decision"] == "browser"
    assert "timed out" in expired["reason"]
    store.close()


def test_background_and_branding_contract_are_packaged() -> None:
    root = Path(__file__).resolve().parents[1]
    background = _background_path()
    manifest = json.loads((root / "assets" / "branding-manifest.json").read_text(encoding="utf-8"))
    package = json.loads((root / "electron" / "package.json").read_text(encoding="utf-8"))

    assert background is not None and background.is_file()
    assert background.name == "backgroud .PNG"
    assert manifest["fit"] == "contain"
    assert manifest["builder_contract"]["reject_distortion"] is True
    assert package["main"] == "main.js"
    packaged_files = set(package["build"]["files"])
    assert {
        "main.js",
        "native-session.js",
        "server-supervisor.js",
        "connection-capacity.js",
        "widget.html",
        "confirm.html",
        "preload-widget.js",
        "preload-confirm.js",
        "update-manager.js",
    } <= packaged_files
    assert not any("v5" in item or "v6" in item or "legacy" in item for item in packaged_files)
    assert any(item.get("to") == "Resouces" for item in package["build"]["extraResources"])


def test_extension_uses_pause_stage_decide_and_browser_fallback() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "browser-extension" / "background-v5.js").read_text(encoding="utf-8")
    loader = (root / "browser-extension" / "background-v4.js").read_text(encoding="utf-8")
    manifest = json.loads((root / "browser-extension" / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["background"]["service_worker"] == "background-v4.js"
    assert 'import "./background-v5.js"' in loader
    assert "/api/v5/browser/capture" in source
    assert "chrome.downloads.pause" in source
    assert "resumeDownload" in source
    assert 'decision==="browser"' in source
    assert "Lumi became unavailable" in source
    assert "/api/downloads/start" not in source


def test_v5_routes_import_from_fresh_source(tmp_path: Path) -> None:
    original_modules = {
        name: module for name, module in sys.modules.items()
        if name == "server" or name.startswith("core.v5")
    }
    for name in list(original_modules):
        sys.modules.pop(name, None)
    try:
        server = importlib.import_module("server")
        rules = {rule.rule for rule in server.app.url_map.iter_rules()}
        assert "/api/v5/browser/capture" in rules
        assert "/api/v5/firmware/search" in rules
        assert "/api/v5/os/search" in rules
    finally:
        for name in list(sys.modules):
            if name == "server" or name.startswith("core.v5"):
                sys.modules.pop(name, None)
        sys.modules.update(original_modules)
