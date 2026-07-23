from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ttg_shell_does_not_mutate_read_only_electron_export():
    source = (ROOT / "electron" / "ttg-shell-bootstrap.js").read_text(encoding="utf-8")
    assert 'electron.BrowserWindow =' not in source
    assert 'Object.defineProperty(electron, "BrowserWindow"' not in source
    assert "Module._load" in source
    assert "TTGBrowserWindow" in source
    assert 'title: "Lumi DM"' in source
    assert "frame: false" in source


def test_custom_builder_uses_unpacked_payload_not_stock_nsis_installer():
    package = json.loads((ROOT / "electron" / "package.json").read_text(encoding="utf-8"))
    config = json.loads((ROOT / "techguy-build.json").read_text(encoding="utf-8"))
    assert package["scripts"]["pack"].endswith("electron-builder --dir")
    assert config["electron"]["preferredScript"] == "pack"
    assert config["electron"]["packageMode"] == "unpacked-for-custom-installer"
    assert config["installer"]["requireCustomGraphicalInstaller"] is True
    assert config["installer"]["rejectVendorInstallerArtifacts"] is True
    assert config["installer"]["requireRegisteredUninstall"] is True
