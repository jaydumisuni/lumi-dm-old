"""Performance and lifecycle hardening for Lumi authentication."""
from __future__ import annotations

from datetime import datetime, timezone
import time

from .security import AuthContext, SecurityManager, _digest, _safe_role


def _install_auth_write_throttle() -> None:
    if getattr(SecurityManager, "_lumi_auth_throttled", False):
        return

    def authenticate(self: SecurityManager, token: str) -> AuthContext | None:
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
        for record in records:
            if record.get("token_hash") != digest or record.get("revoked"):
                continue
            expires_at = float(record.get("expires_at") or 0)
            if expires_at and expires_at <= now:
                record["revoked"] = True
                self._save_token_records(records)
                return None

            last_seen = str(record.get("last_seen_at") or "")
            last_seen_seconds = 0.0
            if last_seen:
                try:
                    parsed = datetime.fromisoformat(last_seen)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    last_seen_seconds = parsed.timestamp()
                except ValueError:
                    last_seen_seconds = 0.0
            if now - last_seen_seconds >= 60:
                record["last_seen_at"] = datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                )
                self._save_token_records(records)

            return AuthContext(
                role=_safe_role(str(record.get("role") or "read_only")),
                client_name=str(record.get("client_name") or "Paired client"),
                token_kind="paired",
                token_id=str(record.get("id") or ""),
            )
        return None

    SecurityManager.authenticate = authenticate
    SecurityManager._lumi_auth_throttled = True


def install() -> None:
    _install_auth_write_throttle()


install()
