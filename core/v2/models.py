"""Core domain models for Lumi DM v2.

The model is independent from Flask, Electron and packaging. Every interface uses
one persisted task contract, while sensitive replay data remains in the vault.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TaskType(str, Enum):
    HTTP = "http"
    FTP = "ftp"
    TORRENT = "torrent"
    VIDEO = "video"
    HLS = "hls"
    PROVIDER = "provider"


class TaskStatus(str, Enum):
    STAGED = "staged"
    QUEUED = "queued"
    RESOLVING = "resolving"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    NEEDS_LINK = "needs_link"
    VERIFYING = "verifying"
    POST_PROCESSING = "post_processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}


@dataclass(slots=True)
class RequestEnvelope:
    """Everything required to replay a browser or direct download request."""

    url: str
    original_page: str = ""
    final_url: str = ""
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    secret_headers_reference: str = ""
    cookie_reference: str = ""
    post_body_reference: str = ""
    captured_at: str = field(default_factory=utc_now)
    provider_id: str = ""
    browser_profile: str = ""
    suggested_filename: str = ""
    proxy_url: str = ""

    def normalized_headers(self, *, include_secrets: bool = True) -> dict[str, str]:
        blocked = {"host", "content-length", "connection"}
        headers = {
            str(key): str(value)
            for key, value in self.headers.items()
            if str(key).strip() and str(key).lower() not in blocked
        }
        if include_secrets and self.secret_headers_reference:
            from .vault import hydrate_secret_headers

            headers.update(hydrate_secret_headers(self.secret_headers_reference))
        return headers

    def redacted_dict(self) -> dict[str, Any]:
        """Return a public view without making API availability depend on the vault.

        A damaged or temporarily unavailable vault must block replay, but it must not
        make task listing or diagnostics crash. When decryption is unavailable, the
        public response exposes only a generic redacted marker.
        """
        out = asdict(self)
        sensitive = {"authorization", "cookie", "proxy-authorization"}
        try:
            visible_headers = self.normalized_headers(include_secrets=True)
            redacted_headers = {
                key: ("<redacted>" if key.lower() in sensitive else value)
                for key, value in visible_headers.items()
            }
        except Exception:
            redacted_headers = self.normalized_headers(include_secrets=False)
            if self.secret_headers_reference:
                redacted_headers["Sensitive-Headers"] = "<redacted-unavailable>"
        out["headers"] = redacted_headers
        for key in (
            "secret_headers_reference",
            "cookie_reference",
            "post_body_reference",
        ):
            if out[key]:
                out[key] = "<secure-reference>"
        if out["proxy_url"]:
            out["proxy_url"] = "<configured>"
        return out

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "RequestEnvelope":
        value = dict(value or {})
        return cls(
            url=str(value.get("url") or ""),
            original_page=str(value.get("original_page") or ""),
            final_url=str(value.get("final_url") or ""),
            method=str(value.get("method") or "GET").upper(),
            headers={
                str(key): str(item)
                for key, item in dict(value.get("headers") or {}).items()
            },
            secret_headers_reference=str(
                value.get("secret_headers_reference") or ""
            ),
            cookie_reference=str(value.get("cookie_reference") or ""),
            post_body_reference=str(value.get("post_body_reference") or ""),
            captured_at=str(value.get("captured_at") or utc_now()),
            provider_id=str(value.get("provider_id") or ""),
            browser_profile=str(value.get("browser_profile") or ""),
            suggested_filename=str(value.get("suggested_filename") or ""),
            proxy_url=str(value.get("proxy_url") or ""),
        )


@dataclass(slots=True)
class SegmentState:
    start: int
    end: int
    downloaded: int = 0
    status: str = "pending"
    attempts: int = 0
    worker_id: str = ""
    last_error: str = ""

    @property
    def length(self) -> int:
        return max(0, self.end - self.start + 1)

    @property
    def remaining(self) -> int:
        return max(0, self.length - self.downloaded)

    @property
    def next_byte(self) -> int:
        return self.start + self.downloaded

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SegmentState":
        return cls(
            start=int(value["start"]),
            end=int(value["end"]),
            downloaded=max(0, int(value.get("downloaded") or 0)),
            status=str(value.get("status") or "pending"),
            attempts=max(0, int(value.get("attempts") or 0)),
            worker_id=str(value.get("worker_id") or ""),
            last_error=str(value.get("last_error") or ""),
        )


@dataclass(slots=True)
class DownloadTask:
    id: str
    type: str
    status: str
    request: RequestEnvelope
    filename: str
    target_dir: str
    temp_dir: str
    final_path: str
    partial_path: str
    queue_id: str = "default"
    category_id: str = "other"
    host_profile_id: str = ""
    priority: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    started_at: str = ""
    finished_at: str = ""
    total_bytes: int = 0
    downloaded_bytes: int = 0
    speed_bytes_per_sec: float = 0.0
    progress_percent: float = 0.0
    connections: int = 1
    max_speed_bps: int = 0
    mode: str = ""
    error: str = ""
    error_code: str = ""
    etag: str = ""
    last_modified: str = ""
    content_type: str = ""
    range_supported: bool = False
    backend_id: str = ""
    duplicate_policy: str = "rename"
    post_process: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 2

    def touch(self) -> None:
        self.updated_at = utc_now()

    def to_dict(self, *, public: bool = False) -> dict[str, Any]:
        out = asdict(self)
        out["request"] = (
            self.request.redacted_dict() if public else asdict(self.request)
        )
        out["url"] = self.request.url
        out["final_url"] = self.request.final_url
        out["path"] = self.final_path
        out["partial_path"] = self.partial_path
        out["pause_requested"] = self.status in {
            TaskStatus.PAUSING.value,
            TaskStatus.PAUSED.value,
        }
        out["cancel_requested"] = self.status in {
            TaskStatus.CANCELLING.value,
            TaskStatus.CANCELLED.value,
        }
        return out

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DownloadTask":
        value = dict(value)
        request = RequestEnvelope.from_dict(
            value.get("request")
            or {
                "url": value.get("url", ""),
                "final_url": value.get("final_url", ""),
            }
        )
        known = {
            "id", "type", "status", "filename", "target_dir", "temp_dir",
            "final_path", "partial_path", "queue_id", "category_id",
            "host_profile_id", "priority", "created_at", "updated_at",
            "started_at", "finished_at", "total_bytes", "downloaded_bytes",
            "speed_bytes_per_sec", "progress_percent", "connections",
            "max_speed_bps", "mode", "error", "error_code", "etag",
            "last_modified", "content_type", "range_supported", "backend_id",
            "duplicate_policy", "post_process", "metadata", "schema_version",
        }
        kwargs = {key: value[key] for key in known if key in value}
        kwargs.setdefault("temp_dir", value.get("target_dir", ""))
        kwargs.setdefault("final_path", value.get("path", ""))
        kwargs.setdefault("partial_path", value.get("partial_path", ""))
        kwargs["request"] = request
        return cls(**kwargs)
