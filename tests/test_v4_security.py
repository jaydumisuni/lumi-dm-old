from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request

from core.v2.store import StateStore
from core.v4.security import SecurityManager, install_security


def test_pairing_code_is_one_time_and_tokens_can_be_revoked(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    manager = SecurityManager(store)
    pairing = manager.create_pairing_code(
        role="read_only",
        client_name="Workshop tablet",
    )

    paired = manager.exchange_pairing_code(
        code=pairing["code"],
        requested_name="Tablet 1",
        remote_addr="192.168.1.20",
    )

    context = manager.authenticate(paired["token"])
    assert context is not None
    assert context.role == "read_only"
    assert context.client_name == "Tablet 1"

    try:
        manager.exchange_pairing_code(
            code=pairing["code"],
            requested_name="Replay",
            remote_addr="192.168.1.20",
        )
        raise AssertionError("pairing code replay unexpectedly succeeded")
    except PermissionError:
        pass

    assert manager.revoke(paired["token_id"])
    assert manager.authenticate(paired["token"]) is None
    store.close()


def _secured_app(manager: SecurityManager) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return "public"

    @app.post("/api/security/pair")
    def pair_placeholder():
        return jsonify({"public": True})

    @app.route("/api/test", methods=["GET", "POST"])
    def protected():
        return jsonify({"method": request.method})

    install_security(app, manager)
    return app


def test_security_guard_enforces_auth_roles_origins_and_extension_cors(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "data")
    manager = SecurityManager(store)
    app = _secured_app(manager)
    client = app.test_client()

    assert client.get("/").status_code == 200
    assert client.get("/api/test").status_code == 401

    session_token, _context = manager.bootstrap("127.0.0.1", "pytest")
    client.set_cookie("lumi_session", session_token)
    assert client.get("/api/test").status_code == 200
    assert client.post(
        "/api/test",
        headers={"Origin": "http://localhost"},
    ).status_code == 200
    assert client.post(
        "/api/test",
        headers={"Origin": "https://evil.example"},
    ).status_code == 403

    pairing = manager.create_pairing_code(
        role="read_only",
        client_name="Read-only dashboard",
    )
    read_only = manager.exchange_pairing_code(
        code=pairing["code"],
        requested_name="Dashboard",
        remote_addr="192.168.1.30",
    )
    authorization = {"Authorization": f"Bearer {read_only['token']}"}
    separate = app.test_client()
    assert separate.get("/api/test", headers=authorization).status_code == 200
    assert separate.post("/api/test", headers=authorization).status_code == 403

    preflight = separate.options(
        "/api/test",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnop",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 204
    assert preflight.headers["Access-Control-Allow-Origin"].startswith(
        "chrome-extension://"
    )
    store.close()


def test_bootstrap_rejects_non_loopback_clients(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    manager = SecurityManager(store)
    try:
        manager.bootstrap("192.168.1.55", "remote")
        raise AssertionError("remote bootstrap unexpectedly succeeded")
    except PermissionError:
        pass
    store.close()
