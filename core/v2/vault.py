"""Encrypted local replay-secret vault for Lumi DM.

Secrets never enter task JSON, public API responses or ordinary logs. The vault
uses a per-installation Fernet key and owner-only file modes where supported.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import secrets
import threading
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


_REFERENCE_PREFIX = "lumi-vault:v1:"


class VaultError(RuntimeError):
    pass


def _encode_path(path: Path) -> str:
    raw = str(path.resolve()).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_path(value: str) -> Path:
    padding = "=" * (-len(value) % 4)
    return Path(base64.urlsafe_b64decode(value + padding).decode("utf-8"))


class LocalSecretVault:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "vault"
        self.root.mkdir(parents=True, exist_ok=True)
        self.key_path = self.root / "vault.key"
        self.entries_path = self.root / "entries.json"
        self._lock = threading.RLock()
        self._fernet = Fernet(self._load_or_create_key())
        if not self.entries_path.exists():
            self._atomic_write({})

    def _load_or_create_key(self) -> bytes:
        try:
            key = self.key_path.read_bytes().strip()
            Fernet(key)
            return key
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            raise VaultError("The local vault key is unreadable or invalid") from exc

        key = Fernet.generate_key()
        temporary = self.key_path.with_suffix(".key.tmp")
        temporary.write_bytes(key)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.key_path)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    def _read_entries(self) -> dict[str, str]:
        try:
            value = json.loads(self.entries_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise VaultError("The local secret vault is damaged") from exc
        if not isinstance(value, dict):
            raise VaultError("The local secret vault has an invalid structure")
        return {str(key): str(item) for key, item in value.items()}

    def _atomic_write(self, entries: dict[str, str]) -> None:
        temporary = self.entries_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(entries, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.entries_path)
        try:
            os.chmod(self.entries_path, 0o600)
        except OSError:
            pass

    def put(self, payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        token = self._fernet.encrypt(encoded).decode("ascii")
        item_id = secrets.token_urlsafe(18)
        with self._lock:
            entries = self._read_entries()
            entries[item_id] = token
            self._atomic_write(entries)
        return f"{_REFERENCE_PREFIX}{_encode_path(self.root)}:{item_id}"

    def get(self, reference: str) -> dict[str, Any]:
        root, item_id = parse_reference(reference)
        if root.resolve() != self.root.resolve():
            raise VaultError("The secret reference belongs to a different vault")
        with self._lock:
            token = self._read_entries().get(item_id)
        if token is None:
            raise VaultError("The requested secret is missing")
        try:
            value = json.loads(self._fernet.decrypt(token.encode("ascii")))
        except (InvalidToken, ValueError, TypeError) as exc:
            raise VaultError("The requested secret cannot be decrypted") from exc
        if not isinstance(value, dict):
            raise VaultError("The requested secret has an invalid structure")
        return value

    def replace(self, reference: str, payload: dict[str, Any]) -> str:
        new_reference = self.put(payload)
        if reference:
            self.delete(reference)
        return new_reference

    def delete(self, reference: str) -> None:
        root, item_id = parse_reference(reference)
        if root.resolve() != self.root.resolve():
            return
        with self._lock:
            entries = self._read_entries()
            if entries.pop(item_id, None) is not None:
                self._atomic_write(entries)


def parse_reference(reference: str) -> tuple[Path, str]:
    if not reference.startswith(_REFERENCE_PREFIX):
        raise VaultError("Unsupported secret reference")
    raw = reference[len(_REFERENCE_PREFIX) :]
    try:
        encoded_root, item_id = raw.rsplit(":", 1)
        root = _decode_path(encoded_root)
    except Exception as exc:
        raise VaultError("Malformed secret reference") from exc
    if not item_id:
        raise VaultError("Malformed secret reference")
    return root, item_id


def resolve_secret(reference: str) -> dict[str, Any]:
    root, _item_id = parse_reference(reference)
    return LocalSecretVault(root.parent).get(reference)


def secure_request_envelope(
    data_dir: Path,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Move sensitive request fields into encrypted storage.

    Existing references are merged so host credentials cannot discard cookies or
    authorization captured earlier in the browser flow.
    """
    value = dict(envelope or {})
    headers = {
        str(key): str(item)
        for key, item in dict(value.get("headers") or {}).items()
    }
    secret_names = {"authorization", "cookie", "proxy-authorization"}
    secret_headers: dict[str, str] = {}
    existing_reference = str(value.get("secret_headers_reference") or "")
    if existing_reference:
        secret_headers.update(hydrate_secret_headers(existing_reference))
    secret_headers.update(
        {
            key: item
            for key, item in headers.items()
            if key.lower() in secret_names
        }
    )
    value["headers"] = {
        key: item
        for key, item in headers.items()
        if key.lower() not in secret_names
    }

    vault = LocalSecretVault(data_dir)
    if secret_headers:
        value["secret_headers_reference"] = vault.replace(
            existing_reference,
            {"headers": secret_headers},
        )

    if value.get("post_body") not in (None, ""):
        existing_post = str(value.get("post_body_reference") or "")
        value["post_body_reference"] = vault.replace(
            existing_post,
            {"post_body": value.pop("post_body")},
        )
    value.pop("cookies", None)
    return value


def hydrate_secret_headers(reference: str) -> dict[str, str]:
    if not reference:
        return {}
    value = resolve_secret(reference)
    return {
        str(key): str(item)
        for key, item in dict(value.get("headers") or {}).items()
    }


def hydrate_post_body(reference: str) -> Any:
    if not reference:
        return None
    body = resolve_secret(reference).get("post_body")
    if not isinstance(body, dict):
        return body
    kind = str(body.get("kind") or "")
    if kind == "form":
        data = body.get("data") or {}
        return {str(key): value for key, value in dict(data).items()}
    if kind == "base64":
        try:
            return base64.b64decode(str(body.get("data") or ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise VaultError("Captured POST body is not valid base64") from exc
    if kind == "text":
        return str(body.get("data") or "")
    return body
