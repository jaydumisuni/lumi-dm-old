"""Torrent metadata, file selection and transfer service for Lumi DM v2."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
import subprocess
import threading
import time
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlparse

import requests

from .models import DownloadTask, TaskStatus, utc_now
from .store import StateStore
from .tools import find_aria2c

try:
    import libtorrent as _lt
except ImportError:  # pragma: no cover - optional runtime
    _lt = None


_MAX_TORRENT_BYTES = 8 * 1024 * 1024


class TorrentUnavailable(RuntimeError):
    pass


class BencodeError(ValueError):
    pass


def bdecode(data: bytes) -> Any:
    """Decode the bencode subset used by torrent metadata."""
    index = 0

    def parse() -> Any:
        nonlocal index
        if index >= len(data):
            raise BencodeError("Unexpected end of bencode data")
        token = data[index:index + 1]
        if token == b"i":
            index += 1
            end = data.find(b"e", index)
            if end < 0:
                raise BencodeError("Unterminated integer")
            raw = data[index:end]
            index = end + 1
            try:
                return int(raw)
            except ValueError as exc:
                raise BencodeError("Invalid integer") from exc
        if token == b"l":
            index += 1
            values = []
            while data[index:index + 1] != b"e":
                values.append(parse())
            index += 1
            return values
        if token == b"d":
            index += 1
            values = {}
            while data[index:index + 1] != b"e":
                key = parse()
                if not isinstance(key, bytes):
                    raise BencodeError("Dictionary key must be bytes")
                values[key] = parse()
            index += 1
            return values
        if token.isdigit():
            colon = data.find(b":", index)
            if colon < 0:
                raise BencodeError("Invalid byte string")
            try:
                length = int(data[index:colon])
            except ValueError as exc:
                raise BencodeError("Invalid byte string length") from exc
            index = colon + 1
            end = index + length
            if end > len(data):
                raise BencodeError("Truncated byte string")
            value = data[index:end]
            index = end
            return value
        raise BencodeError(f"Unexpected bencode token: {token!r}")

    value = parse()
    if index != len(data):
        raise BencodeError("Trailing bencode data")
    return value


def bencode(value: Any) -> bytes:
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(
            bencode(key) + bencode(value[key]) for key in sorted(value)
        ) + b"e"
    raise TypeError(type(value).__name__)


def _text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _torrent_bytes(source: str) -> bytes:
    path = Path(source)
    if path.is_file():
        data = path.read_bytes()
    elif source.startswith(("http://", "https://")):
        with requests.get(source, timeout=(15, 30), stream=True) as response:
            response.raise_for_status()
            chunks = []
            total = 0
            for chunk in response.iter_content(256 * 1024):
                total += len(chunk)
                if total > _MAX_TORRENT_BYTES:
                    raise ValueError("Torrent metadata exceeds 8 MiB")
                chunks.append(chunk)
        data = b"".join(chunks)
    else:
        raise FileNotFoundError(source)
    if len(data) > _MAX_TORRENT_BYTES:
        raise ValueError("Torrent metadata exceeds 8 MiB")
    return data


def inspect_torrent(source: str) -> dict[str, Any]:
    if source.startswith("magnet:"):
        query = parse_qs(urlparse(source).query)
        return {
            "source": source,
            "magnet": True,
            "name": unquote_plus((query.get("dn") or ["Magnet download"])[0]),
            "info_hash": (query.get("xt") or [""])[0].removeprefix("urn:btih:"),
            "trackers": query.get("tr") or [],
            "metadata_pending": True,
            "files": [],
            "total_size": 0,
        }

    data = _torrent_bytes(source)
    decoded = bdecode(data)
    if not isinstance(decoded, dict) or b"info" not in decoded:
        raise BencodeError("Torrent has no info dictionary")
    info = decoded[b"info"]
    if not isinstance(info, dict):
        raise BencodeError("Torrent info is invalid")
    name = _text(info.get(b"name.utf-8") or info.get(b"name") or b"Torrent")
    files = []
    total = 0
    multi = info.get(b"files")
    if isinstance(multi, list):
        for index, item in enumerate(multi):
            if not isinstance(item, dict):
                continue
            length = max(0, int(item.get(b"length") or 0))
            raw_parts = item.get(b"path.utf-8") or item.get(b"path") or []
            path = "/".join(_text(part) for part in raw_parts)
            files.append(
                {
                    "index": index,
                    "path": path,
                    "size": length,
                    "priority": 1,
                    "selected": True,
                }
            )
            total += length
    else:
        length = max(0, int(info.get(b"length") or 0))
        files.append(
            {
                "index": 0,
                "path": name,
                "size": length,
                "priority": 1,
                "selected": True,
            }
        )
        total = length

    announce_list = []
    for tier in decoded.get(b"announce-list") or []:
        if isinstance(tier, list):
            announce_list.extend(_text(item) for item in tier)
    announce = _text(decoded.get(b"announce"))
    if announce and announce not in announce_list:
        announce_list.insert(0, announce)
    return {
        "source": source,
        "magnet": False,
        "name": name,
        "comment": _text(decoded.get(b"comment.utf-8") or decoded.get(b"comment")),
        "created_by": _text(decoded.get(b"created by")),
        "creation_date": int(decoded.get(b"creation date") or 0),
        "piece_length": int(info.get(b"piece length") or 0),
        "private": bool(info.get(b"private")),
        "info_hash": sha1(bencode(info)).hexdigest(),
        "trackers": announce_list,
        "files": files,
        "total_size": total,
        "metadata_pending": False,
    }


@dataclass(slots=True)
class TorrentPlan:
    selected_file_indexes: list[int] | None = None
    file_priorities: dict[int, int] | None = None
    ratio_limit: float = 0.0
    seed_time_minutes: int = 0
    upload_limit_bps: int = 0
    download_limit_bps: int = 0
    stop_after_download: bool = True

    @classmethod
    def from_task(cls, task: DownloadTask) -> "TorrentPlan":
        value = task.metadata
        return cls(
            selected_file_indexes=[
                int(item) for item in list(value.get("selected_file_indexes") or [])
            ] or None,
            file_priorities={
                int(key): int(item)
                for key, item in dict(value.get("file_priorities") or {}).items()
            } or None,
            ratio_limit=max(0.0, float(value.get("ratio_limit") or 0)),
            seed_time_minutes=max(0, int(value.get("seed_time_minutes") or 0)),
            upload_limit_bps=max(0, int(value.get("upload_limit_bps") or 0)),
            download_limit_bps=max(0, int(value.get("download_limit_bps") or 0)),
            stop_after_download=bool(value.get("stop_after_download", True)),
        )


class TorrentService:
    def __init__(self, store: StateStore):
        self.store = store

    def inspect(self, source: str) -> dict[str, Any]:
        return inspect_torrent(source)

    def run(
        self,
        task: DownloadTask,
        pause_event: threading.Event,
        cancel_event: threading.Event,
    ) -> DownloadTask:
        plan = TorrentPlan.from_task(task)
        if _lt is not None:
            return self._run_libtorrent(task, plan, pause_event, cancel_event)
        aria2 = find_aria2c()
        if aria2:
            return self._run_aria2(task, plan, pause_event, cancel_event, aria2)
        raise TorrentUnavailable(
            "Torrent support requires libtorrent or aria2c"
        )

    def _run_aria2(
        self,
        task: DownloadTask,
        plan: TorrentPlan,
        pause_event: threading.Event,
        cancel_event: threading.Event,
        aria2: str,
    ) -> DownloadTask:
        command = [
            aria2,
            "--summary-interval=1",
            "--console-log-level=notice",
            "--file-allocation=none",
            f"--dir={task.target_dir}",
            "--seed-time=0" if plan.stop_after_download else f"--seed-time={plan.seed_time_minutes}",
            task.request.url,
        ]
        if plan.selected_file_indexes:
            command.insert(-1, "--select-file=" + ",".join(
                str(index + 1) for index in sorted(set(plan.selected_file_indexes))
            ))
        if plan.ratio_limit:
            command.insert(-1, f"--seed-ratio={plan.ratio_limit}")
        if plan.upload_limit_bps:
            command.insert(-1, f"--max-upload-limit={plan.upload_limit_bps}")
        if plan.download_limit_bps:
            command.insert(-1, f"--max-download-limit={plan.download_limit_bps}")

        task.status = TaskStatus.RUNNING.value
        task.started_at = task.started_at or utc_now()
        task.mode = "aria2-torrent"
        self.store.save_task(task)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        try:
            for line in process.stdout:
                if cancel_event.is_set() or pause_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    task.status = (
                        TaskStatus.CANCELLED.value
                        if cancel_event.is_set()
                        else TaskStatus.PAUSED.value
                    )
                    task.finished_at = utc_now() if cancel_event.is_set() else ""
                    self.store.save_task(task)
                    return task
                task.metadata["last_backend_line"] = line.strip()[-500:]
                self._parse_aria_progress(task, line)
                self.store.save_task(task)
            code = process.wait()
            if code != 0:
                raise RuntimeError(f"aria2c exited with code {code}")
            task.status = TaskStatus.COMPLETED.value
            task.progress_percent = 100.0
            task.finished_at = utc_now()
            task.speed_bytes_per_sec = 0.0
            self.store.save_task(task)
            return task
        except Exception as exc:
            task.status = TaskStatus.FAILED.value
            task.error = str(exc)
            task.error_code = "torrent_failed"
            task.finished_at = utc_now()
            self.store.save_task(task)
            return task

    @staticmethod
    def _parse_aria_progress(task: DownloadTask, line: str) -> None:
        import re

        progress = re.search(r"\((\d+)%\)", line)
        speed = re.search(r"DL:([^\s\]]+)", line)
        seeders = re.search(r"SEED:(\d+)", line)
        if progress:
            task.progress_percent = float(progress.group(1))
        if speed:
            task.metadata["download_speed_text"] = speed.group(1)
        if seeders:
            task.metadata["seeders"] = int(seeders.group(1))

    def _run_libtorrent(
        self,
        task: DownloadTask,
        plan: TorrentPlan,
        pause_event: threading.Event,
        cancel_event: threading.Event,
    ) -> DownloadTask:
        session = _lt.session()
        settings = session.get_settings()
        if plan.upload_limit_bps:
            settings["upload_rate_limit"] = plan.upload_limit_bps
        if plan.download_limit_bps:
            settings["download_rate_limit"] = plan.download_limit_bps
        session.apply_settings(settings)

        params: Any
        source = task.request.url
        if source.startswith("magnet:"):
            params = _lt.parse_magnet_uri(source)
            params.save_path = task.target_dir
            handle = session.add_torrent(params)
        else:
            data = _torrent_bytes(source)
            info = _lt.torrent_info(_lt.bdecode(data))
            handle = session.add_torrent({"ti": info, "save_path": task.target_dir})

        task.status = TaskStatus.RUNNING.value
        task.started_at = task.started_at or utc_now()
        task.mode = "libtorrent"
        self.store.save_task(task)

        metadata_applied = False
        seed_started = 0.0
        while True:
            if cancel_event.is_set():
                session.remove_torrent(handle, option=1)
                task.status = TaskStatus.CANCELLED.value
                task.finished_at = utc_now()
                self.store.save_task(task)
                return task
            if pause_event.is_set():
                handle.pause()
                task.status = TaskStatus.PAUSED.value
                self.store.save_task(task)
                return task

            status = handle.status()
            if status.has_metadata and not metadata_applied:
                metadata_applied = True
                count = handle.torrent_file().num_files()
                priorities = [1] * count
                if plan.selected_file_indexes is not None:
                    selected = set(plan.selected_file_indexes)
                    priorities = [1 if index in selected else 0 for index in range(count)]
                for index, priority in (plan.file_priorities or {}).items():
                    if 0 <= index < count:
                        priorities[index] = max(0, min(7, priority))
                handle.prioritize_files(priorities)

            task.progress_percent = round(float(status.progress) * 100, 2)
            task.downloaded_bytes = int(status.total_done)
            task.total_bytes = max(task.total_bytes, int(status.total_wanted))
            task.speed_bytes_per_sec = float(status.download_rate)
            task.metadata.update(
                {
                    "upload_speed_bps": int(status.upload_rate),
                    "uploaded_bytes": int(status.total_upload),
                    "peers": int(status.num_peers),
                    "seeds": int(status.num_seeds),
                    "distributed_copies": float(status.distributed_copies),
                    "state": str(status.state),
                }
            )
            self.store.save_task(task)

            if status.is_seeding:
                if not seed_started:
                    seed_started = time.monotonic()
                ratio = status.total_upload / max(1, status.total_done)
                task.metadata["ratio"] = round(ratio, 3)
                if plan.stop_after_download:
                    break
                if plan.ratio_limit and ratio >= plan.ratio_limit:
                    break
                if (
                    plan.seed_time_minutes
                    and time.monotonic() - seed_started >= plan.seed_time_minutes * 60
                ):
                    break
            time.sleep(0.5)

        task.status = TaskStatus.COMPLETED.value
        task.progress_percent = 100.0
        task.finished_at = utc_now()
        task.speed_bytes_per_sec = 0.0
        self.store.save_task(task)
        return task
