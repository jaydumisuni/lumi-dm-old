"""Local API authentication, origin policy and LAN pairing for Lumi DM."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import secrets
import threading
from typing import Any
from urllib.parse import urlparse

from flask import Blueprint, Flask, g, jsonify, make_response, request

from .models import utc_now
from .store import StateStore


_TOKEN_SETTINGS_KEY = "security.clients.v2"
_PAIRING_TTL_SECONDS = 300
_COOKIE_NAME = "lumi_token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_PATHS = {
    "/api/health",
    "/api/auth/bootstrap",
    "/api/auth/pair",
}


@dataclass(slots=True)
class ClientRecord:
    id: str
    name: str
    token_hash: str
    scope: str
    created_at: str
    last_seen_at: str
    revoked: bool = False
    kind: str = "client"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ClientRecord":
        scope = str(value.get("scope") or "read")
        if scope not in {"read", "write", "owner"}:
            scope = "read"
        return cls(
            id=str(value["id"]),
            name=str(value.get("name") or "Lumi client"),
            token_hash=str(value.get("token_hash") or ""),
            scope=scope,
            created_at=str(value.get("created_at") or utc_now()),
            last_seen_at=str(value.get("last_seen_at") or ""),
            revoked=bool(value.get("revoked", False)),
            kind=str(value.get("kind") or "client"),
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "scope": self.scope,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "revoked": self.revoked,
            "kind": self.kind,
        }


@dataclass(slots=True)
class PairingCode:
    code_hash: str
    name: str
    scope: str
    expires_at: datetime


class AuthManager:
    def __init__(self, store: StateStore):
        self.store = store
        self._lock = threading.RLock()
        self._pairings: dict[str, PairingCode] = {}

    @staticmethod
    def hash_token(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def is_loopback(value: str | None) -> bool:
        try:
            return ipaddress.ip_address(value or "").is_loopback
        except ValueError:
            return False

    def _load(self) -> list[ClientRecord]:
        value = self.store.get_setting(_TOKEN_SETTINGS_KEY, [])
        return [ClientRecord.from_dict(item) for item in list(value or [])]

    def _save(self, records: list[ClientRecord]) -> None:
        self.store.set_setting(
            _TOKEN_SETTINGS_KEY,
            [
                {
                    "id": item.id,
                    "name": item.name,
                    "token_hash": item.token_hash,
                    "scope": item.scope,
                    "created_at": item.created_at,
                    "last_seen_at": item.last_seen_at,
                    "revoked": item.revoked,
                    "kind": item.kind,
                }
                for item in records
            ],
        )

    def issue(
        self,
        name: str,
        *,
        scope: str,
        kind: str,
    ) -> tuple[str, ClientRecord]:
        if scope not in {"read", "write", "owner"}:
            raise ValueError("scope must be read, write or owner")
        raw = secrets.token_urlsafe(32)
        record = ClientRecord(
            id=secrets.token_urlsafe(12),
            name=name.strip()[:100] or "Lumi client",
            token_hash=self.hash_token(raw),
            scope=scope,
            created_at=utc_now(),
            last_seen_at=utc_now(),
            kind=kind,
        )
        with self._lock:
            records = self._load()
            records.append(record)
            # Keep revoked history bounded without breaking active clients.
            active = [item for item in records if not item.revoked]
            revoked = [item for item in records if item.revoked][-100:]
            self._save(active + revoked)
        return raw, record

    def bootstrap_local(
        self,
        remote_addr: str | None,
        *,
        name: str,
        kind: str = "browser",
    ) -> tuple[str, ClientRecord]:
        if not self.is_loopback(remote_addr):
            raise PermissionError("Local bootstrap is restricted to loopback clients")
        return self.issue(name, scope="owner", kind=kind)

    def validate(self, raw_token: str) -> ClientRecord | None:
        if not raw_token:
            return None
        digest = self.hash_token(raw_token)
        with self._lock:
            records = self._load()
            matched = None
            changed = False
            for item in records:
                if not item.revoked and secrets.compare_digest(item.token_hash, digest):
                    item.last_seen_at = utc_now()
                    matched = item
                    changed = True
                    break
            if changed:
                self._save(records)
            return matched

    def list_clients(self) -> list[dict[str, Any]]:
        return [item.public_dict() for item in self._load()]

    def revoke(self, client_id: str) -> bool:
        with self._lock:
            records = self._load()
            found = False
            for item in records:
                if item.id == client_id:
                    item.revoked = True
                    found = True
            if found:
                self._save(records)
            return found

    def create_pairing_code(
        self,
        *,
        name: str,
        scope: str,
    ) -> dict[str, Any]:
        if scope not in {"read", "write"}:
            raise ValueError("LAN pairing scope must be read or write")
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = self.hash_token(code)
        expires = datetime.now(timezone.utc) + timedelta(seconds=_PAIRING_TTL_SECONDS)
        pairing = PairingCode(
            code_hash=code_hash,
            name=name.strip()[:100] or "Paired device",
            scope=scope,
            expires_at=expires,
        )
        with self._lock:
            self._cleanup_pairings()
            self._pairings[code_hash] = pairing
        return {
            "code": code,
            "name": pairing.name,
            "scope": pairing.scope,
            "expires_at": expires.isoformat(timespec="seconds"),
        }

    def exchange_pairing_code(self, code: str) -> tuple[str, ClientRecord]:
        digest = self.hash_token(code.strip())
        with self._lock:
            self._cleanup_pairings()
            pairing = self._pairings.pop(digest, None)
        if pairing is None:
            raise PermissionError("Pairing code is invalid or expired")
        return self.issue(
            pairing.name,
            scope=pairing.scope,
            kind="lan",
        )

    def _cleanup_pairings(self) -> None:
        now = datetime.now(timezone.utc)
        self._pairings = {
            key: item
            for key, item in self._pairings.items()
            if item.expires_at > now
        }


def _bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return str(request.cookies.get(_COOKIE_NAME) or "")


def _origin_allowed() -> bool:
    origin = request.headers.get("Origin", "")
    if not origin:
        return True
    if origin.startswith(("chrome-extension://", "moz-extension://")):
        return True
    try:
        parsed = urlparse(origin)
        return parsed.netloc.lower() == request.host.lower()
    except ValueError:
        return False


def _scope_allows(scope: str, method: str) -> bool:
    if method in _SAFE_METHODS:
        return True
    return scope in {"write", "owner"}


def configure_security(app: Flask, store: StateStore) -> AuthManager:
    manager = AuthManager(store)
    app.extensions["lumi_auth"] = manager

    @app.before_request
    def enforce_lumi_security():
        if request.method == "OPTIONS":
            return make_response("", 204)
        if not _origin_allowed():
            return jsonify({"error": "origin not allowed"}), 403
        if request.path in _PUBLIC_PATHS or request.path.startswith("/static/") or request.path == "/":
            return None
        record = manager.validate(_bearer_token())
        if record is None:
            return jsonify({"error": "authentication required"}), 401
        if not _scope_allows(record.scope, request.method):
            return jsonify({"error": "client is read-only"}), 403
        g.lumi_client = record
        return None

    @app.after_request
    def apply_lumi_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self' http://127.0.0.1:* http://localhost:*",
        )
        origin = request.headers.get("Origin", "")
        if origin.startswith(("chrome-extension://", "moz-extension://")):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        return response

    return manager


auth_blueprint = Blueprint("lumi_auth", __name__)


def _manager() -> AuthManager:
    return request.app.extensions["lumi_auth"]  # type: ignore[attr-defined]


def _auth_manager() -> AuthManager:
    from flask import current_app

    return current_app.extensions["lumi_auth"]


@auth_blueprint.get("/api/health")
def api_health():
    return jsonify({"status": "ok", "service": "Lumi DM"})


@auth_blueprint.post("/api/auth/bootstrap")
def api_auth_bootstrap():
    value = request.get_json(silent=True) or {}
    try:
        token, record = _auth_manager().bootstrap_local(
            request.remote_addr,
            name=str(value.get("name") or "Lumi local UI"),
            kind=str(value.get("kind") or "browser"),
        )
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    response = jsonify({"token": token, "client": record.public_dict()})
    response.set_cookie(
        _COOKIE_NAME,
        token,
        httponly=True,
        samesite="Strict",
        secure=False,
        max_age=30 * 24 * 60 * 60,
        path="/",
    )
    return response


@auth_blueprint.get("/api/auth/whoami")
def api_auth_whoami():
    record = getattr(g, "lumi_client", None)
    return jsonify({"client": record.public_dict() if record else None})


@auth_blueprint.post("/api/auth/pair-code")
def api_auth_pair_code():
    record = getattr(g, "lumi_client", None)
    if record is None or record.scope != "owner":
        return jsonify({"error": "owner access required"}), 403
    value = request.get_json(silent=True) or {}
    try:
        return jsonify(
            _auth_manager().create_pairing_code(
                name=str(value.get("name") or "Paired Lumi device"),
                scope=str(value.get("scope") or "read"),
            )
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@auth_blueprint.post("/api/auth/pair")
def api_auth_pair():
    value = request.get_json(silent=True) or {}
    try:
        token, record = _auth_manager().exchange_pairing_code(
            str(value.get("code") or "")
        )
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    return jsonify({"token": token, "client": record.public_dict()})


@auth_blueprint.get("/api/auth/clients")
def api_auth_clients():
    record = getattr(g, "lumi_client", None)
    if record is None or record.scope != "owner":
        return jsonify({"error": "owner access required"}), 403
    return jsonify({"clients": _auth_manager().list_clients()})


@auth_blueprint.delete("/api/auth/clients/<client_id>")
def api_auth_revoke(client_id: str):
    record = getattr(g, "lumi_client", None)
    if record is None or record.scope != "owner":
        return jsonify({"error": "owner access required"}), 403
    if record.id == client_id:
        return jsonify({"error": "the current owner session cannot revoke itself"}), 409
    revoked = _auth_manager().revoke(client_id)
    return jsonify({"revoked": revoked, "id": client_id}), (200 if revoked else 404)
