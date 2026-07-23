from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

from core.v3 import executables


ROOT = Path(__file__).resolve().parents[1]


def test_builder_profile_matches_lumi_release_contract():
    config = json.loads((ROOT / "techguy-build.json").read_text(encoding="utf-8"))
    assert config["appName"] == "Lumi DM"
    assert config["appVersion"] == "1.0.0"
    assert config["projectType"] == "electron"
    assert config["repository"] == "jaydumisuni/lumi-dm"
    assert config["entryFile"] == "electron/main.js"
    assert config["electron"]["preferredScript"] == "pack"
    assert config["electron"]["packageMode"] == "unpacked-for-custom-installer"
    assert config["installer"]["runAsAdmin"] is True
    assert config["installer"]["desktopShortcutChecked"] is True
    assert config["installer"]["startMenuShortcutChecked"] is True
    assert config["installer"]["requireCustomGraphicalInstaller"] is True
    assert config["installer"]["rejectVendorInstallerArtifacts"] is True
    assert config["installer"]["requireRegisteredUninstall"] is True
    assert config["githubRelease"]["tag"] == "v1.0.0"
    assert config["githubRelease"]["generateSha256Sidecars"] is True
    assert config["githubRelease"]["tokenStoredInProject"] is False


def test_builder_sidecar_matches_electron_extra_resources():
    config = json.loads((ROOT / "techguy-build.json").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "electron" / "package.json").read_text(encoding="utf-8"))
    sidecar = config["electron"]["pythonSidecars"][0]
    assert sidecar["entry"] == "server.py"
    assert sidecar["name"] == "LUMIDM-server"
    assert sidecar["output"] == "dist/server"
    assert "libtorrent==2.0.13" in sidecar["extraRequirements"]
    assert any(item.startswith("imageio-ffmpeg") for item in sidecar["extraRequirements"])
    server_resource = next(
        item for item in package["build"]["extraResources"]
        if item.get("to") == "server"
    )
    assert server_resource["from"] == "../dist/server"


def test_ffmpeg_falls_back_to_packaged_imageio_binary(monkeypatch, tmp_path):
    binary = tmp_path / "ffmpeg.exe"
    binary.write_bytes(b"ffmpeg")
    monkeypatch.setattr(executables, "find_executable", lambda *args, **kwargs: None)
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: str(binary)),
    )
    assert executables.find_ffmpeg() == str(binary)


def test_7zip_remains_optional_in_release_profile():
    config = json.loads((ROOT / "techguy-build.json").read_text(encoding="utf-8"))
    sevenzip = next(item for item in config["dependencies"] if item["id"] == "sevenzip")
    assert sevenzip["kind"] == "sevenzip"
    assert sevenzip["required"] is False


def test_notification_flood_guard_is_part_of_installed_extension():
    manifest = json.loads(
        (ROOT / "browser-extension" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["background"]["service_worker"] == "background.js"
    assert manifest["action"]["default_popup"] == "popup.html"
    assert manifest["content_scripts"][0]["js"] == ["content.js", "content-safety.js"]

    background = (ROOT / "browser-extension" / "background.js").read_text(encoding="utf-8")
    assert 'import "./notification-guard.js"' in background
    assert 'import "./browser-bridge.js"' in background

    guard = (ROOT / "browser-extension" / "notification-guard.js").read_text(encoding="utf-8")
    assert "OFFLINE_NOTICE_COOLDOWN_MS" in guard
    assert "DUPLICATE_WINDOW_MS" in guard
    assert "isQuietAutomaticFailure" in guard
    assert "if (isQuietAutomaticFailure(options)) return CONNECTIVITY_ID" in guard
    assert "chrome.notifications.clear" in guard
    assert 'const CONNECTIVITY_ID = "LUMIDM-connectivity-state"' in guard

    for obsolete in (
        "background-v4.js", "background-v5.js", "notification-guard-v6.js",
        "content-v5.js", "popup-v4.html", "popup-v5.js",
    ):
        assert not (ROOT / "browser-extension" / obsolete).exists()


def test_release_candidate_has_no_builder_environment_inside_project():
    forbidden = [ROOT / ".venv", ROOT / "venv", ROOT / "node_modules"]
    assert not any(path.exists() for path in forbidden)
