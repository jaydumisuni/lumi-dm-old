from __future__ import annotations

import json
from pathlib import Path


def test_native_widget_authenticates_before_api_polling_and_is_packaged() -> None:
    root = Path(__file__).resolve().parents[1]
    bootstrap = (root / "electron" / "bootstrap-v5-final.js").read_text(
        encoding="utf-8"
    )
    session = (root / "electron" / "native-session-v5.js").read_text(
        encoding="utf-8"
    )
    package = json.loads(
        (root / "electron" / "package.json").read_text(encoding="utf-8")
    )

    assert bootstrap.index('require("./native-session-v5")') < bootstrap.index(
        'require("./bootstrap-v5")'
    )
    assert "/api/security/bootstrap" in session
    assert 'value.startsWith("lumi_session=")' in session
    assert "options.headers.Cookie = sessionCookie" in session
    assert "http.get = function lumiAuthenticatedGet" in session
    assert "native-session-v5.js" in package["build"]["files"]
    assert "ttg-shell-bootstrap.js" in package["build"]["files"]
    assert "legacy-guards-v5.js" in package["build"]["files"]


def test_native_session_cookie_is_scoped_to_loopback_lumi_port() -> None:
    root = Path(__file__).resolve().parents[1]
    session = (root / "electron" / "native-session-v5.js").read_text(
        encoding="utf-8"
    )

    assert '["127.0.0.1", "localhost", "::1"].includes(host)' in session
    assert "port === 7000" in session
    assert 'route !== "/api/security/bootstrap"' in session


def test_legacy_shell_cannot_restore_old_widget_or_raise_manager_for_clipboard() -> None:
    root = Path(__file__).resolve().parents[1]
    bootstrap = (root / "electron" / "bootstrap-v5-final.js").read_text(
        encoding="utf-8"
    )
    guards = (root / "electron" / "legacy-guards-v5.js").read_text(
        encoding="utf-8"
    )

    assert bootstrap.index('require("./legacy-guards-v5")') < bootstrap.index(
        'require("./bootstrap-v5")'
    )
    assert 'callback.name === "checkClipboard"' in guards
    assert "bounds.width === 220 && bounds.height === 60" in guards
    assert 'stack.includes("showMainWindowForStaged")' in guards
