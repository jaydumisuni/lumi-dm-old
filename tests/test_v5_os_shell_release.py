from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from core.v5 import os_catalog


def test_os_catalogue_has_windows_macos_linux_and_labelled_providers() -> None:
    value = os_catalog.catalogue()
    assert value["families"] == ["Windows", "macOS", "Linux"]
    providers = {item["id"]: item for item in value["providers"]}
    assert providers["windows-fido"]["official"] is False
    assert "GPLv3" in providers["windows-fido"]["attribution"]
    assert providers["microsoft-downloads"]["official"] is True
    assert providers["apple-support"]["official"] is True
    assert providers["ubuntu"]["direct_files"] is True


def test_windows_search_is_explicit_resolver_plus_official_fallback() -> None:
    results = os_catalog.search_os(
        family="Windows",
        version="Windows 11",
        edition="Home/Pro",
        architecture="x64",
        language="English International",
    )
    assert len(results) == 2
    resolver = next(item for item in results if item["provider"] == "windows-fido")
    fallback = next(item for item in results if item["provider"] == "microsoft-downloads")
    assert resolver["direct"] is False
    assert resolver["metadata"]["resolver"] == "fido"
    assert fallback["official"] is True
    assert fallback["direct"] is False


def test_ubuntu_results_keep_official_url_and_sha256(monkeypatch) -> None:
    sample = '''
latest:
  name: "Resolute Raccoon"
  full_version: "26.04"
lts:
  name: "Resolute Raccoon"
  full_version: "26.04"
checksums:
  desktop:
    "26.04": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *ubuntu-26.04-desktop-amd64.iso"
'''
    monkeypatch.setattr(os_catalog._CACHE, "get", lambda _key, _ttl, loader: sample)
    results = os_catalog._ubuntu_results("26.04", "Desktop", "amd64", "all")
    assert results
    item = results[0]
    assert item.official is True
    assert item.url == "https://releases.ubuntu.com/26.04/ubuntu-26.04-desktop-amd64.iso"
    assert item.sha256 == "a" * 64


def test_fido_resolution_refuses_non_windows_platform(monkeypatch) -> None:
    monkeypatch.setattr(os_catalog.platform, "system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="only on Windows"):
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
        "/static/ttg-shell.css",
        "/static/ttg-shell.js",
        "/static/ttg-theme.css",
        "/static/ttg-theme.js",
        "/static/operating-systems.css",
        "/static/operating-systems.js",
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
