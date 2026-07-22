from __future__ import annotations

import json
from pathlib import Path

from core.v2.security_wave4 import AuthManager
from core.v2.store import StateStore


def test_wave4_bootstrap_replaces_previous_same_client(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    manager = AuthManager(store)

    first_token, first = manager.bootstrap_local(
        "127.0.0.1",
        name="Lumi local UI",
        kind="browser",
    )
    second_token, second = manager.bootstrap_local(
        "127.0.0.1",
        name="Lumi local UI",
        kind="browser",
    )

    assert first.id != second.id
    assert manager.validate(first_token) is None
    assert manager.validate(second_token).id == second.id
    active = [item for item in manager.list_clients() if not item["revoked"]]
    assert [item["id"] for item in active] == [second.id]
    store.close()


def test_pwa_shell_is_lumi_branded_and_never_caches_api() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "static" / "manifest.webmanifest").read_text(encoding="utf-8")
    )
    service_worker = (root / "static" / "sw.js").read_text(encoding="utf-8")

    assert manifest["name"] == "Lumi Download Manager"
    assert manifest["short_name"] in {"Lumi DM", "Lumi"}
    assert manifest["start_url"] == "/"
    assert manifest["display"] in {"standalone", "window-controls-overlay"}
    assert manifest["icons"]
    assert 'url.pathname.startsWith("/api/")' in service_worker
    assert 'const CACHE = "lumi-shell-v2"' in service_worker
    assert "/api/" not in service_worker.split("const SHELL", 1)[1].split("];", 1)[0]


def test_product_csp_allows_provider_thumbnails_but_not_remote_scripts() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "server.py").read_text(encoding="utf-8")

    assert "img-src 'self' data: https:" in launcher
    assert "script-src 'self'" in launcher
    assert "script-src 'self' https:" not in launcher
