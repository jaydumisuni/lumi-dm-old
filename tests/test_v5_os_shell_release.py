from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from core.v5 import os_catalog


def test_os_catalogue_lists_three_computer_families() -> None:
    catalogue = os_catalog.catalogue()
    assert catalogue["families"] == ["Windows", "macOS", "Linux"]
    assert "Windows 11" in catalogue["options"]["Windows"]["versions"]
    assert "Full installer" in catalogue["options"]["macOS"]["editions"]
    assert "Ubuntu" in catalogue["options"]["Linux"]["distributions"]


def test_linux_catalogue_returns_official_distribution_sources() -> None:
    results = os_catalog.search(
        family="Linux",
        distribution="Ubuntu",
        version="24.04 LTS",
        edition="Desktop",
        architecture="x64",
        channel="stable",
    )
    assert results
    assert all(item.official for item in results)
    assert all(item.metadata["os_family"] == "Linux" for item in results)
    assert all(item.source_url.startswith("https://") for item in results)


def test_macos_results_label_index_and_apple_hosting() -> None:
    results = os_catalog.search(
        family="macOS",
        version="macOS 15 Sequoia",
        edition="Full installer",
        architecture="Universal",
        channel="public",
    )
    assert results
    assert any(item.provider == "mr-macintosh" for item in results)
    assert all("Apple" in item.notes or "Apple" in item.source_name for item in results)


def test_windows_fido_requires_explicit_resolver(monkeypatch) -> None:
    monkeypatch.setattr(os_catalog, "_find_fido", lambda: None)
    with pytest.raises(os_catalog.OSCatalogError):
        os_catalog.resolve_windows_iso(
            version="Windows 11",
            edition="Home/Pro",
            language="English International",
            architecture="x64",
        )


def test_ttg_shell_and_builder_release_contract_are_packaged() -> None:
    root = Path(__file__).resolve().parents[1]
    shell = json.loads((root / "assets" / "ttg-app-shell-standard.json").read_text(encoding="utf-8"))
    release = json.loads((root / "assets" / "builder-github-release-contract.json").read_text(encoding="utf-8"))
    package = json.loads((root / "electron" / "package.json").read_text(encoding="utf-8"))
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    index = (root / "static" / "index.html").read_text(encoding="utf-8")

    assert shell["window"]["native_frame"] is False
    assert shell["titlebar"]["right"] == [
        "notification_bell", "settings_gear", "separator",
        "minimize", "maximize_restore", "close",
    ]
    assert shell["settings_gear"]["single_settings_entry"] is True
    assert release["security"]["never_store_token_in_project"] is True
    assert release["release"]["generate_sha256_sidecars"] is True
    assert package["main"] == "main.js"
    assert "main.js" in package["build"]["files"]
    assert "frame: false" in main
    assert 'title: "Lumi DM"' in main
    for asset in (
        "/static/ttg-app-shell-v1.css",
        "/static/ttg-app-shell-v1.js",
        "/static/app-os-v5.css",
        "/static/app-os-v5.js",
    ):
        assert asset in index


def test_release_publisher_has_reviewable_cli() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/publish_github_release.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert "--repo" in result.stdout
    assert "--replace-assets" in result.stdout
    assert "--no-checksums" in result.stdout
