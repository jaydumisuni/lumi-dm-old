from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sidebar_separates_mobile_firmware_and_operating_systems() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert 'data-view="firmware"' in html
    assert ">Mobile firmware<" in html
    assert 'data-view="operating_systems"' in html
    assert ">Operating systems<" in html
    assert 'id="view-operating_systems"' in html

    sidebar = html.split('<nav class="nav-list"', 1)[1].split("</nav>", 1)[0]
    assert 'data-view="settings"' not in sidebar
    assert 'data-view="diagnostics"' not in sidebar


def test_operating_system_workspace_is_not_a_firmware_dropdown() -> None:
    source = (ROOT / "static" / "operating-systems.js").read_text(encoding="utf-8")

    assert "viewMeta.operating_systems" in source
    assert 'document.getElementById("view-operating_systems")' in source
    assert 'data-catalogue-mode' not in source
    assert 'sessionStorage.getItem("LUMI.osFamily")' in source
    assert '"Windows", "macOS", "Linux"' in source
    assert "/api/v5/os/windows/resolve" in source
    assert "/api/v5/os/stage" in source


def test_ttg_shell_v2_owns_window_controls_and_gear_surfaces() -> None:
    shell = (ROOT / "static" / "ttg-shell.js").read_text(encoding="utf-8")
    enhancements = (ROOT / "static" / "ttg-theme.js").read_text(encoding="utf-8")
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")

    for expected in (
        'id="ttg-bell"',
        'id="ttg-gear"',
        'data-window-action="minimize"',
        'data-window-action="maximize"',
        'data-window-action="close"',
        'data-shell-action="settings"',
        'data-shell-action="update"',
        'data-shell-action="help"',
        'data-shell-action="about"',
        'data-shell-action="diagnostics"',
    ):
        assert expected in shell

    assert 'data-ttg-theme="system"' in enhancements
    assert 'data-ttg-theme="dark"' in enhancements
    assert 'data-ttg-theme="light"' in enhancements
    assert "frame: false" in main
    assert 'title: "Lumi DM"' in main
    assert "autoHideMenuBar: true" in main


def test_locked_builder_shell_contract_is_v2() -> None:
    contract = json.loads(
        (ROOT / "assets" / "ttg-app-shell-standard.json").read_text(
            encoding="utf-8"
        )
    )

    assert contract["standard_id"] == "ttg-app-shell-v2"
    assert contract["status"] == "locked"
    assert contract["window"]["native_frame"] is False
    assert contract["settings_gear"]["appearance_control"] == [
        "System",
        "Dark",
        "Light",
    ]
    assert contract["navigation"]["settings_in_sidebar"] is False
    assert contract["navigation"]["diagnostics_in_sidebar"] is False
    assert contract["navigation"]["technician_sections"] == [
        "Mobile firmware",
        "Operating systems",
    ]
    assert contract["builder_contract"]["remove_platform_default_titlebar"] is True
    assert contract["builder_contract"]["generate_bell_and_gear_menus"] is True


def test_advanced_diagnostics_remains_available_only_as_hidden_workspace() -> None:
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    shell = (ROOT / "static" / "ttg-shell.js").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "TTG_APP_SHELL_STANDARD.md").read_text(
        encoding="utf-8"
    )

    assert 'id="view-diagnostics"' in html
    assert 'data-shell-action="diagnostics"' in shell
    assert "Gear → Advanced diagnostics" in docs
    assert "database backup and repair" in docs
    assert "missing-file detection" in docs
