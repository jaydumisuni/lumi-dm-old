"""Authentication, origin policy and secure pairing for Lumi DM.

The local UI receives a short-lived HttpOnly session after a loopback-only
bootstrap. Browser extensions and LAN clients pair with a one-time code and use a
persistent bearer token. Read-only tokens cannot change application state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlparse

from flask import Flask, jsonify, make_response, request

from core.v2.models import utc_now
from core.v2.store import StateStore


_SESSION_COOKIE = "lumi_session"
_TOKENS_KEY = "security.paired_tokens.v1"
_MAX_PAIRING_ATTEMPTS = 8


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_loopback(value: str) -> bool:
    try:
        return ipaddress.ip_address(value.split("%", 1)[0]).is_loopback
    except ValueError:
        return value in {"localhost", ""}


def _safe_role(value: str) -> str:
    return value if value in {"owner", "read_only"} else "read_only"


@dataclass(slots=True)
class AuthContext:
    role: str
    client_name: str
    token_kind: str
    token_id: str = ""

    @property
    def can_write(self) -> bool:
        return self.role == "owner"


class SecurityManager:
    def __init__(self, store: StateStore):
        self.store = store
        self._sessions: dict[str, dict[str, Any]] = {}
        self._pairing: dict[str, dict[str, Any]] = {}
        self._failed_pairing: dict[str, list[float]] = {}
        self._lock = threading.RLock()
        if self.store.get_setting(_TOKENS_KEY) is None:
            self.store.set_setting(_TOKENS_KEY, [])

    def _token_records(self) -> list[dict[str, Any]]:
        value = self.store.get_setting(_TOKENS_KEY, [])
        return [dict(item) for item in value or [] if isinstance(item, dict)]

    def _save_token_records(self, records: list[dict[str, Any]]) -> None:
        self.store.set_setting(_TOKENS_KEY, records)

    def bootstrap(self, remote_addr: str, user_agent: str) -> tuple[str, AuthContext]:
        if not _is_loopback(remote_addr):
            raise PermissionError("Local UI bootstrap is restricted to this computer")
        token = secrets.token_urlsafe(40)
        digest = _digest(token)
        expires = time.time() + 12 * 60 * 60
        with self._lock:
            self._sessions[digest] = {
                "role": "owner",
                "client_name": "Lumi local UI",
                "user_agent": user_agent[:300],
                "expires": expires,
            }
        return token, AuthContext("owner", "Lumi local UI", "session")

    def authenticate(self, token: str) -> AuthContext | None:
        if not token:
            return None
        digest = _digest(token)
        now = time.time()
        with self._lock:
            session = self._sessions.get(digest)
            if session:
                if float(session.get("expires") or 0) <= now:
                    self._sessions.pop(digest, None)
                else:
                    return AuthContext(
                        role=_safe_role(str(session.get("role") or "owner")),
                        client_name=str(session.get("client_name") or "Local UI"),
                        token_kind="session",
                    )

        records = self._token_records()
        changed = False
        for record in records:
            if record.get("token_hash") != digest or record.get("revoked"):
                continue
            expires_at = float(record.get("expires_at") or 0)
            if expires_at and expires_at <= now:
                record["revoked"] = True
                changed = True
                break
            record["last_seen_at"] = utc_now()
            changed = True
            context = AuthContext(
                role=_safe_role(str(record.get("role") or "read_only")),
                client_name=str(record.get("client_name") or "Paired client"),
                token_kind="paired",
                token_id=str(record.get("id") or ""),
            )
            if changed:
                self._save_token_records(records)
            return context
        if changed:
            self._save_token_records(records)
        return None

    def create_pairing_code(
        self,
        *,
        role: str,
        client_name: str,
        expires_in: int = 600,
    ) -> dict[str, Any]:
        role = _safe_role(role)
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        code = f"{raw[:4]}-{raw[4:]}"
        expires_at = time.time() + max(60, min(3600, int(expires_in)))
        with self._lock:
            self._pairing[_digest(code)] = {
                "role": role,
                "client_name": client_name.strip()[:120] or "Paired client",
                "expires_at": expires_at,
            }
        return {
            "code": code,
            "role": role,
            "client_name": client_name.strip()[:120] or "Paired client",
            "expires_at": datetime.fromtimestamp(
                expires_at,
                timezone.utc,
            ).isoformat(timespec="seconds"),
        }

    def _allow_pair_attempt(self, remote_addr: str) -> bool:
        now = time.time()
        with self._lock:
            attempts = [
                item
                for item in self._failed_pairing.get(remote_addr, [])
                if now - item <= 10 * 60
            ]
            self._failed_pairing[remote_addr] = attempts
            return len(attempts) < _MAX_PAIRING_ATTEMPTS

    def exchange_pairing_code(
        self,
        *,
        code: str,
        requested_name: str,
        remote_addr: str,
    ) -> dict[str, Any]:
        if not self._allow_pair_attempt(remote_addr):
            raise PermissionError("Too many failed pairing attempts; try again later")
        normalized = code.strip().upper()
        code_hash = _digest(normalized)
        now = time.time()
        with self._lock:
            value = self._pairing.pop(code_hash, None)
            if value is None or float(value.get("expires_at") or 0) <= now:
                self._failed_pairing.setdefault(remote_addr, []).append(now)
                raise PermissionError("Pairing code is invalid or expired")

        token = secrets.token_urlsafe(48)
        record = {
            "id": secrets.token_hex(8),
            "token_hash": _digest(token),
            "role": _safe_role(str(value.get("role") or "read_only")),
            "client_name": (
                requested_name.strip()[:120]
                or str(value.get("client_name") or "Paired client")
            ),
            "remote_addr": remote_addr,
            "created_at": utc_now(),
            "last_seen_at": utc_now(),
            "expires_at": 0,
            "revoked": False,
        }
        records = self._token_records()
        records.append(record)
        self._save_token_records(records)
        return {
            "token": token,
            "token_id": record["id"],
            "role": record["role"],
            "client_name": record["client_name"],
        }

    def list_clients(self) -> list[dict[str, Any]]:
        clients = []
        for record in self._token_records():
            public = dict(record)
            public.pop("token_hash", None)
            clients.append(public)
        clients.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return clients

    def revoke(self, token_id: str) -> bool:
        records = self._token_records()
        found = False
        for record in records:
            if str(record.get("id") or "") == token_id:
                record["revoked"] = True
                record["revoked_at"] = utc_now()
                found = True
        if found:
            self._save_token_records(records)
        return found

    def cleanup(self) -> None:
        now = time.time()
        with self._lock:
            self._sessions = {
                key: value
                for key, value in self._sessions.items()
                if float(value.get("expires") or 0) > now
            }
            self._pairing = {
                key: value
                for key, value in self._pairing.items()
                if float(value.get("expires_at") or 0) > now
            }


def _extract_token() -> tuple[str, str]:
    authorization = str(request.headers.get("Authorization") or "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip(), "bearer"
    return str(request.cookies.get(_SESSION_COOKIE) or ""), "cookie"


def _origin_is_same_host(origin: str) -> bool:
    try:
        parsed = urlparse(origin)
        return parsed.netloc.lower() == request.host.lower()
    except ValueError:
        return False


def _extension_origin(origin: str) -> bool:
    return origin.startswith(("chrome-extension://", "moz-extension://"))


def install_security(app: Flask, manager: SecurityManager) -> None:
    if app.extensions.get("lumi_security"):
        return
    app.extensions["lumi_security"] = manager

    @app.before_request
    def _lumi_auth_guard():
        path = request.path
        origin = str(request.headers.get("Origin") or "")
        public = (
            path == "/"
            or path.startswith("/static/")
            or path in {
                "/api/security/bootstrap",
                "/api/security/pair",
            }
        )
        if request.method == "OPTIONS":
            response = make_response("", 204)
            if _extension_origin(origin) or _origin_is_same_host(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Headers"] = (
                    "Authorization, Content-Type, X-Lumi-Client"
                )
                response.headers["Access-Control-Allow-Methods"] = (
                    "GET, POST, PATCH, DELETE, OPTIONS"
                )
                response.headers["Vary"] = "Origin"
            return response
        if public:
            return None

        token, token_kind = _extract_token()
        context = manager.authenticate(token)
        if context is None:
            return jsonify({"error": "authentication required"}), 401

        unsafe = request.method not in {"GET", "HEAD", "OPTIONS"}
        if unsafe and not context.can_write:
            return jsonify({"error": "client is read-only"}), 403
        if token_kind == "cookie" and unsafe:
            if not origin or not _origin_is_same_host(origin):
                return jsonify({"error": "same-origin request required"}), 403
        if origin and not (_origin_is_same_host(origin) or _extension_origin(origin)):
            return jsonify({"error": "request origin is not allowed"}), 403

        request.environ["lumi.auth"] = context
        return None

    @app.after_request
    def _lumi_security_headers(response):
        origin = str(request.headers.get("Origin") or "")
        if origin and (_origin_is_same_host(origin) or _extension_origin(origin)):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        return response


def auth_context() -> AuthContext | None:
    value = request.environ.get("lumi.auth")
    return value if isinstance(value, AuthContext) else None


def session_cookie_name() -> str:
    return _SESSION_COOKIE
