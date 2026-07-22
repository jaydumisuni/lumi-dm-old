"""Privacy-safe runtime diagnostics and export bundles for Lumi DM."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit
import zipfile

from core.v2.models import utc_now
from core.v2.store import StateStore
from core.v3.executables import find_7zip, find_aria2c, find_ffmpeg, find_ffprobe

from .maintenance import MaintenanceService


_SENSITIVE_KEY = re.compile(
    r"authorization|cookie|password|secret|token|post.?body|credential|api.?key",
    re.I,
)
_PATH_KEY = re.compile(r"path|dir|folder|location", re.I)
_URL_IN_TEXT = re.compile(
    r"(?:https?|ftp)://[^\s<>'\"]+|magnet:\?[^\s<>'\"]+",
    re.I,
)
_INLINE_SECRET = re.compile(
    r"(?i)(?:bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"(?:authorization|cookie|password|passwd|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)
_WINDOWS_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/](?:[^\r\n<>:\"|?*]+[\\/]?)+)"
)
_UNIX_PRIVATE_PATH = re.compile(
    r"(?<![A-Za-z0-9])/(?:home|Users|mnt|tmp|var|private|storage|sdcard)/[^\s<>'\"]+"
)


class DiagnosticsService:
    def __init__(
        self,
        store: StateStore,
        maintenance: MaintenanceService,
    ):
        self.store = store
        self.maintenance = maintenance
        self.output_dir = self.store.data_dir / "diagnostics"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_url(value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            return "<url>"
        if parsed.scheme not in {"http", "https", "ftp", "magnet"}:
            return "<url>"
        netloc = parsed.hostname or ""
        if port:
            netloc += f":{port}"
        return urlunsplit(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                "<redacted>" if parsed.query else "",
                "",
            )
        )

    @staticmethod
    def _safe_path(value: str) -> str:
        try:
            path = Path(value)
        except Exception:
            return "<path>"
        name = path.name or "directory"
        return f"<private-path>/{name}"

    def _redact_text(self, value: str) -> str:
        text = _INLINE_SECRET.sub("<redacted>", value)
        text = _URL_IN_TEXT.sub(
            lambda match: self._safe_url(match.group(0)),
            text,
        )
        text = _WINDOWS_PATH.sub(
            lambda match: self._safe_path(match.group(0)),
            text,
        )
        text = _UNIX_PRIVATE_PATH.sub(
            lambda match: self._safe_path(match.group(0)),
            text,
        )
        return text[:2000]

    def redact(self, value: Any, *, key: str = "") -> Any:
        if _SENSITIVE_KEY.search(key):
            return "<redacted>" if value not in (None, "", [], {}) else value
        if isinstance(value, dict):
            return {
                str(item_key): self.redact(item, key=str(item_key))
                for item_key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [self.redact(item, key=key) for item in value]
        if isinstance(value, str):
            if value.startswith(("http://", "https://", "ftp://", "magnet:")):
                return self._safe_url(value)
            if _PATH_KEY.search(key) or os.path.isabs(value):
                return self._safe_path(value)
            return self._redact_text(value)
        return value

    def summary(self) -> dict[str, Any]:
        tasks = self.store.list_tasks(limit=5000)
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        return {
            "generated_at": utc_now(),
            "application": {
                "name": "Lumi Download Manager",
                "runtime_version": "4.0.0-dev",
                "python": platform.python_version(),
                "platform": platform.platform(),
                "architecture": platform.machine(),
                "frozen": bool(getattr(sys, "frozen", False)),
            },
            "engines": {
                "ffmpeg": bool(find_ffmpeg()),
                "ffprobe": bool(find_ffprobe()),
                "seven_zip": bool(find_7zip()),
                "aria2c": bool(find_aria2c()),
            },
            "tasks": {
                "total": len(tasks),
                "by_status": counts,
            },
            "database": self.maintenance.database_health(force=True),
            "storage": self.redact(self.maintenance.storage_health()),
            "missing_files": self.redact(
                self.maintenance.scan_missing_files(mark=False)
            ),
        }

    def _task_evidence(self) -> list[dict[str, Any]]:
        rows = []
        for task in self.store.list_tasks(limit=5000):
            public = task.to_dict(public=True)
            rows.append(
                self.redact(
                    {
                        "id": public.get("id"),
                        "type": public.get("type"),
                        "status": public.get("status"),
                        "filename": public.get("filename"),
                        "category_id": public.get("category_id"),
                        "queue_id": public.get("queue_id"),
                        "created_at": public.get("created_at"),
                        "updated_at": public.get("updated_at"),
                        "finished_at": public.get("finished_at"),
                        "total_bytes": public.get("total_bytes"),
                        "downloaded_bytes": public.get("downloaded_bytes"),
                        "progress_percent": public.get("progress_percent"),
                        "mode": public.get("mode"),
                        "error_code": public.get("error_code"),
                        "error": public.get("error"),
                        "request": public.get("request"),
                        "metadata": public.get("metadata"),
                        "post_process": public.get("post_process"),
                        "events": self.store.list_events(task.id, limit=80),
                    }
                )
            )
        return rows

    def export(self) -> dict[str, Any]:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = self.output_dir / f"lumi-diagnostics-{stamp}.zip"
        temporary = archive.with_suffix(".zip.tmp")
        summary = self.summary()
        evidence = self._task_evidence()
        manifest = {
            "generated_at": utc_now(),
            "privacy": (
                "Authentication data, cookies, credentials, query strings and "
                "private filesystem locations are removed from this bundle."
            ),
            "files": ["summary.json", "tasks.json"],
        }
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as bundle:
            bundle.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True),
            )
            bundle.writestr(
                "summary.json",
                json.dumps(summary, indent=2, sort_keys=True),
            )
            bundle.writestr(
                "tasks.json",
                json.dumps(evidence, indent=2, sort_keys=True),
            )
        os.replace(temporary, archive)
        return {
            "path": str(archive),
            "bytes": archive.stat().st_size,
            "generated_at": manifest["generated_at"],
        }
