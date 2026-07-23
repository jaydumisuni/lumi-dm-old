from __future__ import annotations

import json
from pathlib import Path


def test_native_widget_authenticates_before_api_polling_and_is_packaged() -> None:
    root = Path(__file__).resolve().parents[1]
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    session = (root / "electron" / "native-session.js").read_text(encoding="utf-8")
    package = json.loads(
        (root / "electron" / "package.json").read_text(encoding="utf-8")
    )

    assert 'require("./native-session")' in main
    assert main.index('require("./native-session")') < main.index('require("./server-supervisor")')
    assert "/api/security/bootstrap" in session
    assert 'value.startsWith("lumi_session=")' in session
    assert "options.headers.Cookie = sessionCookie" in session
    assert "http.get = function lumiAuthenticatedGet" in session
    assert "native-session.js" in package["build"]["files"]
    assert "main.js" in package["build"]["files"]
    assert "preload-widget.js" in package["build"]["files"]


def test_native_session_cookie_is_scoped_to_loopback_lumi_port() -> None:
    root = Path(__file__).resolve().parents[1]
    session = (root / "electron" / "native-session.js").read_text(encoding="utf-8")

    assert '["127.0.0.1", "localhost", "::1"].includes(host)' in session
    assert "port === 7000" in session
    assert 'route !== "/api/security/bootstrap"' in session


def test_old_widget_clipboard_and_staged_manager_paths_are_absent() -> None:
    root = Path(__file__).resolve().parents[1]
    main = (root / "electron" / "main.js").read_text(encoding="utf-8")
    package = json.loads((root / "electron" / "package.json").read_text(encoding="utf-8"))

    for obsolete in (
        "checkClipboard",
        "showMainWindowForStaged",
        "widgetWindow = new BrowserWindow({\n    width: 220, height: 60",
        "Reminal Download Manager",
        "legacy-guards-v5.js",
        "bootstrap-v5.js",
    ):
        assert obsolete not in main
        assert obsolete not in package["build"]["files"]
    assert 'task.status === "browser_pending"' in main
    assert 'loadFile(path.join(__dirname, "confirm.html"))' in main
