from __future__ import annotations

from pathlib import Path

from core.v6.reliability import infer_filename, normalize_user_filename


ROOT = Path(__file__).resolve().parents[1]


def test_pdf_extension_and_rfc5987_filename_are_recovered():
    assert infer_filename(
        disposition="attachment; filename*=UTF-8''Service%20Manual",
        final_url="https://example.test/download?id=4",
        content_type="application/pdf; charset=binary",
    ) == "Service Manual.pdf"


def test_final_redirect_url_beats_generic_download_name():
    assert infer_filename(
        disposition='attachment; filename="download.bin"',
        final_url="https://cdn.example.test/files/firmware-package.zip?token=abc",
        content_type="application/zip",
    ) == "firmware-package.zip"


def test_user_edited_filename_keeps_name_and_receives_known_extension():
    assert normalize_user_filename(
        "Samsung service sheet",
        content_type="application/pdf",
        final_url="https://example.test/file",
    ) == "Samsung service sheet.pdf"


def test_invalid_windows_filename_characters_are_sanitized():
    assert infer_filename(
        disposition='attachment; filename="AUX:manual?.pdf"',
        final_url="https://example.test/file",
        content_type="application/pdf",
    ) == "AUX_manual_.pdf"


def test_consolidated_desktop_runtime_is_packaged_without_legacy_bootstraps():
    package = (ROOT / "electron" / "package.json").read_text(encoding="utf-8")
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    assert '"main": "main.js"' in package
    for filename in (
        "native-session.js",
        "server-supervisor.js",
        "connection-capacity.js",
        "widget.html",
        "confirm.html",
    ):
        assert filename in package
    for obsolete in (
        "bootstrap-v5-final.js",
        "bootstrap-v5.js",
        "legacy-guards-v5.js",
        "notification-baseline-v6.js",
        "widget-v5.html",
        "confirm-v5.html",
        "Reminal Download Manager",
    ):
        assert obsolete not in package
        assert obsolete not in main


def test_widget_distinguishes_live_use_from_connection_capacity():
    widget = (ROOT / "electron" / "widget.html").read_text(encoding="utf-8")
    assert "Live ↓" in widget
    assert "Capacity ↓" in widget
    assert "Upload capacity" not in widget
    assert "Open Lumi Manager" in widget
    assert "Resume in manager" in widget


def test_boot_notifications_use_a_session_transition_baseline():
    shell = (ROOT / "static" / "ttg-shell.js").read_text(encoding="utf-8")
    main = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    assert "taskBaseline" in shell
    assert "ACTIVE_STATES.has(previous)" in shell
    assert "New work, warnings and updates from this session" in shell
    assert "baselineReady" in main
    assert 'status === "completed" && previous && ACTIVE_STATES.has(previous)' in main


def test_startup_supervisor_reconnects_fallback_windows():
    source = (ROOT / "electron" / "server-supervisor.js").read_text(encoding="utf-8")
    assert "consecutiveFailures >= 3" in source
    assert "restartAttempts < 6" in source
    assert 'url.startsWith("file:")' in source
    assert 'window.loadURL("http://127.0.0.1:7000")' in source
    assert "function start()" in source
    assert "function stop()" in source


def test_about_points_to_the_official_tools_page_and_verified_releases():
    shell = (ROOT / "static" / "ttg-shell.js").read_text(encoding="utf-8")
    assert "https://thetechguyds.com/tools" in shell
    assert "verified GitHub Releases" in shell


def test_connection_test_is_manual_bounded_and_blocks_active_downloads():
    source = (ROOT / "electron" / "connection-capacity.js").read_text(encoding="utf-8")
    assert "Pause active downloads before testing connection capacity" in source
    assert "15_000_000" in source
    assert "5_000_000" in source
    assert "download_mbps" in source and "upload_mbps" in source
    assert "latency_ms" in source
