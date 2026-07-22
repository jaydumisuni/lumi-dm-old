"""Per-host transfer, capture, proxy and credential policies."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .models import RequestEnvelope
from .store import StateStore
from .vault import LocalSecretVault


@dataclass(slots=True)
class HostProfile:
    id: str
    name: str
    host_pattern: str
    enabled: bool = True
    max_connections: int = 0
    max_running: int = 0
    speed_limit_bps: int = 0
    user_agent: str = ""
    proxy_url: str = ""
    username_reference: str = ""
    password_reference: str = ""
    intercept_mode: str = "auto"  # auto, always_lumi, always_browser
    retry_limit: int = 3

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HostProfile":
        mode = str(value.get("intercept_mode") or "auto")
        if mode not in {"auto", "always_lumi", "always_browser"}:
            mode = "auto"
        return cls(
            id=str(value["id"]),
            name=str(value.get("name") or value["id"]),
            host_pattern=str(value.get("host_pattern") or "").lower(),
            enabled=bool(value.get("enabled", True)),
            max_connections=max(0, int(value.get("max_connections") or 0)),
            max_running=max(0, int(value.get("max_running") or 0)),
            speed_limit_bps=max(0, int(value.get("speed_limit_bps") or 0)),
            user_agent=str(value.get("user_agent") or ""),
            proxy_url=str(value.get("proxy_url") or ""),
            username_reference=str(value.get("username_reference") or ""),
            password_reference=str(value.get("password_reference") or ""),
            intercept_mode=mode,
            retry_limit=max(0, int(value.get("retry_limit") or 3)),
        )

    def to_dict(self, *, public: bool = False) -> dict[str, Any]:
        value = asdict(self)
        if public:
            if value["proxy_url"]:
                value["proxy_url"] = "<configured>"
            for key in ("username_reference", "password_reference"):
                if value[key]:
                    value[key] = "<secure-reference>"
        return value

    def matches(self, host: str) -> bool:
        if not self.enabled or not self.host_pattern:
            return False
        host = host.lower()
        pattern = self.host_pattern.lower()
        return fnmatch(host, pattern) or fnmatch(host, f"*.{pattern}")


class HostProfileManager:
    SETTINGS_KEY = "host_profiles.v2"

    def __init__(self, store: StateStore, data_dir: Path):
        self.store = store
        self.vault = LocalSecretVault(data_dir)
        if self.store.get_setting(self.SETTINGS_KEY) is None:
            self.store.set_setting(self.SETTINGS_KEY, [])

    def list(self) -> list[HostProfile]:
        value = self.store.get_setting(self.SETTINGS_KEY, [])
        return [HostProfile.from_dict(item) for item in list(value or [])]

    def get(self, profile_id: str) -> HostProfile | None:
        return next((item for item in self.list() if item.id == profile_id), None)

    def match_url(self, url: str) -> HostProfile | None:
        host = (urlparse(url).hostname or "").lower()
        matches = [item for item in self.list() if item.matches(host)]
        if not matches:
            return None
        return max(matches, key=lambda item: len(item.host_pattern))

    def save(
        self,
        profile: HostProfile,
        *,
        username: str | None = None,
        password: str | None = None,
    ) -> HostProfile:
        if username is not None:
            if profile.username_reference:
                self.vault.delete(profile.username_reference)
            profile.username_reference = (
                self.vault.put({"username": username}) if username else ""
            )
        if password is not None:
            if profile.password_reference:
                self.vault.delete(profile.password_reference)
            profile.password_reference = (
                self.vault.put({"password": password}) if password else ""
            )

        profiles = self.list()
        replaced = False
        for index, existing in enumerate(profiles):
            if existing.id == profile.id:
                profiles[index] = profile
                replaced = True
                break
        if not replaced:
            profiles.append(profile)
        self.store.set_setting(
            self.SETTINGS_KEY,
            [item.to_dict() for item in profiles],
        )
        return profile

    def delete(self, profile_id: str) -> None:
        profile = self.get(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        for reference in (
            profile.username_reference,
            profile.password_reference,
        ):
            if reference:
                self.vault.delete(reference)
        self.store.set_setting(
            self.SETTINGS_KEY,
            [item.to_dict() for item in self.list() if item.id != profile_id],
        )

    def apply(
        self,
        envelope: RequestEnvelope,
        *,
        connections: int,
        speed_limit_bps: int,
    ) -> tuple[RequestEnvelope, int, int, HostProfile | None]:
        profile = self.match_url(envelope.url)
        if profile is None:
            return envelope, connections, speed_limit_bps, None
        if profile.user_agent:
            envelope.headers["User-Agent"] = profile.user_agent
        if profile.proxy_url:
            envelope.proxy_url = profile.proxy_url
        if profile.username_reference and profile.password_reference:
            username = self.vault.get(profile.username_reference).get("username", "")
            password = self.vault.get(profile.password_reference).get("password", "")
            if username or password:
                import base64

                raw = f"{username}:{password}".encode("utf-8")
                envelope.headers["Authorization"] = (
                    "Basic " + base64.b64encode(raw).decode("ascii")
                )
        effective_connections = profile.max_connections or connections
        effective_speed = profile.speed_limit_bps or speed_limit_bps
        return envelope, effective_connections, effective_speed, profile
