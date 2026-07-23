from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from core.v2.store import StateStore
from core.v5.api import BrowserHandoffService, _background_path
from core.v5 import firmware


class FakeRuntime:
    def __init__(self, store: StateStore):
        self.store = store

    def get_task(self, _task_id: str):
        return None


def test_firmware_catalogue_is_deterministic_and_source_labelled() -> None:
    providers = firmware.providers()
    ids = {item["id"] for item in providers}

    assert {
        "apple-ipsw",
        "google-pixel",
        "lineageos",
        "grapheneos",
        "eos",
        "androidfilehost",
        "needrom",
        "xda",
    } <= ids
    assert "Apple" in firmware.brands()
    assert "Samsung" in firmware.brands()
    assert "Google Pixel" in firmware.brands()
    source_results = firmware._search_sources("Samsung", "SM-S918B", "", "")
    assert all(item.source_group for item in source_results)
    assert all(item.direct is False for item in source_results)


def test_apple_adapter_keeps_signing_and_source_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        firmware,
        "_get_json",
        lambda url: {
            "firmwares": [
                {
                    "version": "18.6",
                    "buildid": "22G86",
                    "url": "https://updates.cdn-apple.com/iPhone15,2_18.6_22G86_Restore.ipsw",
                    "filesize": 7_000_000_000,
                    "signed": True,
                    "sha256sum": "a" * 64,
                    "releasedate": "2026-07-15T00:00:00Z",
                }
            ]
        },
    )

    results = firmware._apple_firmware("iPhone15,2", "stable")

    assert results
    item = results[0]
    assert item.signed is True
    assert item.sha256 == "a" * 64
    assert item.source_name == "IPSW.me"
    assert item.url.endswith(".ipsw")
    assert item.device == "iPhone15,2"


def test_google_parser_keeps_direct_file_and_checksum(monkeypatch) -> None:
    html = """
    <table><tr><td>Pixel 8 shiba AP4A.260701.001</td>
    <td><a href="https://dl.google.com/dl/android/aosp/shiba-factory.zip">Download</a></td>
    <td>bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb</td></tr></table>
    """
    monkeypatch.setattr(firmware._CACHE, "get", lambda _key, _ttl, loader: html)

    results = firmware._parse_google_page(
        "https://developers.google.com/android/images",
        "shiba",
        "stable",
        "factory image",
    )

    assert len(results) == 1
    assert results[0].official is True
    assert results[0].sha256 == "b" * 64
    assert results[0].build == "AP4A.260701.001"
    assert results[0].url.startswith("https://dl.google.com/")


def test_browser_handoff_times_out_to_browser_and_persists_decisions(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    service = BrowserHandoffService(FakeRuntime(store))

    created = service.create(
        task_id="pending-task",
        browser_download_id=42,
        original_url="https://example.invalid/file.zip",
    )
    assert service.get(created["id"])["decision"] == "pending"

    confirmed = service.decide(created["id"], "lumi", "confirmed")
    assert confirmed["decision"] == "lumi"

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
    source = (root / "browser-extension" / "browser-bridge.js").read_text(encoding="utf-8")
    loader = (root / "browser-extension" / "background.js").read_text(encoding="utf-8")
    manifest = json.loads((root / "browser-extension" / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["background"]["service_worker"] == "background.js"
    assert 'import "./browser-bridge.js"' in loader
    assert 'import "./notification-guard.js"' in loader
    assert "/api/v5/browser/capture" in source
    assert "chrome.downloads.pause" in source
    assert "resumeDownload" in source
    assert 'decision==="browser"' in source
    assert "Lumi became unavailable" in source
    assert "/api/downloads/start" not in source


def test_v5_routes_import_from_fresh_source(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["LUMIDM_DATA_DIR"] = str(tmp_path / "server-data")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import server; "
                "routes={rule.rule for rule in server.app.url_map.iter_rules()}; "
                "required={'/api/v5/branding/background','/api/v5/firmware/catalogue',"
                "'/api/v5/firmware/search','/api/v5/browser/capture',"
                "'/api/v5/desktop/command'}; "
                "assert required <= routes, required-routes"
            ),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
