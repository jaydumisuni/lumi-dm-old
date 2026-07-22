"""Wave 4 authentication hardening over the stable security contract."""
from __future__ import annotations

from datetime import datetime, timezone
import secrets

from . import security as _base
from .models import utc_now


class AuthManager(_base.AuthManager):
    """Bound local bootstrap sessions and avoid write-amplifying API polling."""

    def bootstrap_local(
        self,
        remote_addr: str | None,
        *,
        name: str,
        kind: str = "browser",
    ):
        if not self.is_loopback(remote_addr):
            raise PermissionError("Local bootstrap is restricted to loopback clients")
        clean_name = name.strip()[:100] or "Lumi local UI"
        with self._lock:
            records = self._load()
            changed = False
            for record in records:
                if (
                    not record.revoked
                    and record.kind == kind
                    and record.name == clean_name
                ):
                    record.revoked = True
                    changed = True
            if changed:
                self._save(records)
        return self.issue(clean_name, scope="owner", kind=kind)

    def validate(self, raw_token: str):
        if not raw_token:
            return None
        digest = self.hash_token(raw_token)
        with self._lock:
            records = self._load()
            matched = None
            changed = False
            now = datetime.now(timezone.utc)
            for record in records:
                if record.revoked or not secrets.compare_digest(record.token_hash, digest):
                    continue
                matched = record
                try:
                    previous = datetime.fromisoformat(record.last_seen_at)
                    if previous.tzinfo is None:
                        previous = previous.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    previous = datetime.min.replace(tzinfo=timezone.utc)
                if (now - previous).total_seconds() >= 60:
                    record.last_seen_at = utc_now()
                    changed = True
                break
            if changed:
                self._save(records)
            return matched


# The base factory and blueprint resolve AuthManager at call time. Replacing the
# module binding keeps one implementation surface while activating Wave 4 rules.
_base.AuthManager = AuthManager

auth_blueprint = _base.auth_blueprint
configure_security = _base.configure_security

__all__ = ["AuthManager", "auth_blueprint", "configure_security"]
