"""Torrent metadata, selective files, peer telemetry and seeding controls."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable, Protocol

import requests

from core.v2.models import DownloadTask, TaskStatus, utc_now
from core.v2.store import StateStore

from .executables import find_aria2c


try:
    import libtorrent as lt
except ImportError:  # pragma: no cover - optional capability
    lt = None


class TorrentError(RuntimeError):
    pass


@dataclass(slots=True)
class TorrentFile:
    index: int
    path: str
    size: int
    selected: bool = True
    priority: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "path": self.path,
            "size": self.size,
            "selected": self.selected,
            "priority": self.priority,
        }


@dataclass(slots=True)
class TorrentSnapshot:
    name: str = ""
    state: str = "resolving_metadata"
    metadata_ready: bool = False
    progress: float = 0.0
    total_bytes: int = 0
    downloaded_bytes: int = 0
    uploaded_bytes: int = 0
    download_rate: float = 0.0
    upload_rate: float = 0.0
    peers: int = 0
    seeds: int = 0
    ratio: float = 0.0
    finished: bool = False
    seeding: bool = False
    files: list[TorrentFile] = field(default_factory=list)
    output_path: str = ""
    error: str = ""


class TorrentBackend(Protocol):
    def poll(self) -> TorrentSnapshot: ...
    def pause(self) -> None: ...
    def cancel(self) -> None: ...
    def save_resume(self) -> None: ...
    def close(self) -> None: ...


def _torrent_info_from_source(source: str):
    if lt is None:
        raise TorrentError("libtorrent is not installed")
    if source.startswith(("http://", "https://")):
        response = requests.get(source, timeout=(15, 60))
        response.raise_for_status()
        return lt.torrent_info(lt.bdecode(response.content))
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(source)
    return lt.torrent_info(str(path))


class TorrentInspector:
    def inspect(self, source: str) -> dict[str, Any]:
        cleaned = str(source or "").strip()
        if cleaned.startswith("magnet:"):
            name = "Magnet download"
            if "dn=" in cleaned:
                from urllib.parse import parse_qs, urlparse

                name = parse_qs(urlparse(cleaned).query).get("dn", [name])[0]
            return {
                "source": cleaned,
                "name": name,
                "metadata_pending": True,
                "files": [],
                "backend": "libtorrent" if lt is not None else ("aria2c" if find_aria2c() else None),
            }
        if lt is None:
            return {
                "source": cleaned,
                "name": Path(cleaned).name or "Torrent",
                "metadata_pending": True,
                "files": [],
                "backend": "aria2c" if find_aria2c() else None,
            }
        info = _torrent_info_from_source(cleaned)
        storage = info.files()
        files = [
            TorrentFile(
                index=index,
                path=str(storage.file_path(index)),
                size=int(storage.file_size(index)),
            ).to_dict()
            for index in range(storage.num_files())
        ]
        return {
            "source": cleaned,
            "name": str(info.name()),
            "metadata_pending": False,
            "piece_count": int(info.num_pieces()),
            "piece_length": int(info.piece_length()),
            "total_bytes": sum(item["size"] for item in files),
            "files": files,
            "backend": "libtorrent",
        }


class LibtorrentBackend:
    def __init__(
        self,
        task: DownloadTask,
        data_dir: Path,
    ):
        if lt is None:
            raise TorrentError("libtorrent is not installed")
        self.task = task
        self.data_dir = Path(data_dir)
        self.resume_path = self.data_dir / "resume" / f"{task.id}.torrent.fastresume"
        self.resume_path.parent.mkdir(parents=True, exist_ok=True)
        self.session = lt.session()
        try:
            self.session.apply_settings(
                {
                    "alert_mask": int(lt.alert.category_t.all_categories),
                    "enable_dht": True,
                    "enable_lsd": True,
                    "enable_upnp": True,
                    "enable_natpmp": True,
                }
            )
        except Exception:
            pass
        self.handle = self._add_torrent(task.request.url, Path(task.target_dir))
        self._priorities_applied = False
        self._cancelled = False

    def _add_torrent(self, source: str, target_dir: Path):
        params = None
        if self.resume_path.is_file():
            try:
                params = lt.read_resume_data(self.resume_path.read_bytes())
            except Exception:
                params = None
        if params is None:
            if source.startswith("magnet:"):
                params = lt.parse_magnet_uri(source)
            else:
                params = lt.add_torrent_params()
                params.ti = _torrent_info_from_source(source)
        params.save_path = str(target_dir)
        return self.session.add_torrent(params)

    def _files(self) -> list[TorrentFile]:
        if not self.handle.has_metadata():
            return []
        info = self.handle.torrent_file()
        storage = info.files()
        selected = self.task.metadata.get("selected_files")
        selected_set = (
            {int(item) for item in selected}
            if isinstance(selected, list) and selected
            else set(range(storage.num_files()))
        )
        priorities = list(self.task.metadata.get("file_priorities") or [])
        files = []
        for index in range(storage.num_files()):
            priority = (
                int(priorities[index])
                if index < len(priorities)
                else (1 if index in selected_set else 0)
            )
            files.append(
                TorrentFile(
                    index=index,
                    path=str(storage.file_path(index)),
                    size=int(storage.file_size(index)),
                    selected=priority > 0,
                    priority=priority,
                )
            )
        return files

    def _apply_priorities(self) -> None:
        if self._priorities_applied or not self.handle.has_metadata():
            return
        files = self._files()
        try:
            self.handle.prioritize_files([item.priority for item in files])
        except Exception:
            pass
        self._priorities_applied = True

    def poll(self) -> TorrentSnapshot:
        if self._cancelled:
            return TorrentSnapshot(state="cancelled", error="cancelled")
        status = self.handle.status()
        metadata_ready = bool(self.handle.has_metadata())
        if metadata_ready:
            self._apply_priorities()
        files = self._files() if metadata_ready else []
        downloaded = int(getattr(status, "total_wanted_done", 0) or getattr(status, "total_done", 0) or 0)
        total = int(getattr(status, "total_wanted", 0) or sum(item.size for item in files))
        uploaded = int(getattr(status, "total_upload", 0) or 0)
        ratio = uploaded / max(1, downloaded)
        state_value = getattr(status, "state", "")
        state = str(state_value).split(".")[-1]
        seeding = bool(getattr(status, "is_seeding", False))
        finished = seeding or (total > 0 and downloaded >= total)
        name = str(self.handle.name() or self.task.filename or "Torrent")
        output = str(Path(self.task.target_dir) / name)
        return TorrentSnapshot(
            name=name,
            state="seeding" if seeding else state,
            metadata_ready=metadata_ready,
            progress=float(getattr(status, "progress", 0.0) or 0.0),
            total_bytes=total,
            downloaded_bytes=downloaded,
            uploaded_bytes=uploaded,
            download_rate=float(getattr(status, "download_rate", 0.0) or 0.0),
            upload_rate=float(getattr(status, "upload_rate", 0.0) or 0.0),
            peers=int(getattr(status, "num_peers", 0) or 0),
            seeds=int(getattr(status, "num_seeds", 0) or 0),
            ratio=ratio,
            finished=finished,
            seeding=seeding,
            files=files,
            output_path=output,
        )

    def pause(self) -> None:
        try:
            self.handle.pause()
        finally:
            self.save_resume()

    def cancel(self) -> None:
        self._cancelled = True
        self.save_resume()
        try:
            self.session.remove_torrent(self.handle)
        except Exception:
            pass

    def save_resume(self) -> None:
        if not self.handle.is_valid() or not self.handle.has_metadata():
            return
        try:
            self.handle.save_resume_data()
            deadline = time.time() + 5
            while time.time() < deadline:
                alert = self.session.wait_for_alert(500)
                if alert is None:
                    continue
                for current in self.session.pop_alerts():
                    if hasattr(lt, "save_resume_data_alert") and isinstance(current, lt.save_resume_data_alert):
                        buffer = lt.write_resume_data_buf(current.params)
                        self.resume_path.write_bytes(bytes(buffer))
                        return
                    if hasattr(lt, "save_resume_data_failed_alert") and isinstance(current, lt.save_resume_data_failed_alert):
                        return
        except Exception:
            return

    def close(self) -> None:
        self.save_resume()


class Aria2Backend:
    def __init__(self, task: DownloadTask, data_dir: Path):
        del data_dir
        binary = find_aria2c()
        if not binary:
            raise TorrentError("Torrent support requires libtorrent or aria2c")
        self.task = task
        command = [
            binary,
            "--dir", str(task.target_dir),
            "--continue=true",
            "--summary-interval=1",
            "--console-log-level=warn",
            "--download-result=hide",
            "--file-allocation=none",
            "--enable-color=false",
        ]
        selected = task.metadata.get("selected_files") or []
        if selected:
            command.append("--select-file=" + ",".join(str(int(item) + 1) for item in selected))
        ratio = float(task.metadata.get("seed_ratio") or 0.0)
        command.append(f"--seed-ratio={ratio}")
        command.append(task.request.url)
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.started = time.monotonic()
        self.cancelled = False

    def poll(self) -> TorrentSnapshot:
        code = self.process.poll()
        size = 0
        for item in Path(self.task.target_dir).rglob("*"):
            if item.is_file() and not item.name.endswith(".aria2"):
                try:
                    size += item.stat().st_size
                except OSError:
                    pass
        if code is None:
            return TorrentSnapshot(
                name=self.task.filename,
                state="downloading",
                metadata_ready=False,
                downloaded_bytes=size,
                output_path=self.task.target_dir,
            )
        return TorrentSnapshot(
            name=self.task.filename,
            state="completed" if code == 0 else "failed",
            metadata_ready=False,
            downloaded_bytes=size,
            total_bytes=size,
            progress=1.0 if code == 0 else 0.0,
            finished=code == 0,
            output_path=self.task.target_dir,
            error="" if code == 0 else f"aria2c exited with code {code}",
        )

    def pause(self) -> None:
        self.process.terminate()

    def cancel(self) -> None:
        self.cancelled = True
        self.process.terminate()

    def save_resume(self) -> None:
        return

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()


def default_backend(task: DownloadTask, data_dir: Path) -> TorrentBackend:
    if lt is not None:
        return LibtorrentBackend(task, data_dir)
    return Aria2Backend(task, data_dir)


class TorrentRunner:
    def __init__(
        self,
        store: StateStore,
        data_dir: Path,
        task_id: str,
        *,
        pause_event: threading.Event,
        cancel_event: threading.Event,
        backend_factory: Callable[[DownloadTask, Path], TorrentBackend] = default_backend,
        poll_interval: float = 0.5,
    ):
        self.store = store
        self.data_dir = Path(data_dir)
        self.task_id = task_id
        self.pause_event = pause_event
        self.cancel_event = cancel_event
        self.backend_factory = backend_factory
        self.poll_interval = max(0.05, float(poll_interval))

    def _task(self) -> DownloadTask:
        task = self.store.get_task(self.task_id)
        if task is None:
            raise KeyError(self.task_id)
        return task

    def _save(self, task: DownloadTask, event: str = "") -> None:
        self.store.save_task(task)
        if event:
            self.store.append_event(task.id, event)

    def run(self) -> None:
        task = self._task()
        task.status = TaskStatus.RESOLVING.value
        task.started_at = task.started_at or utc_now()
        task.mode = "torrent"
        self._save(task, "torrent_resolving")
        backend: TorrentBackend | None = None
        seeding_started = 0.0
        try:
            backend = self.backend_factory(task, self.data_dir)
            while True:
                if self.cancel_event.is_set():
                    backend.cancel()
                    task = self._task()
                    task.status = TaskStatus.CANCELLED.value
                    task.finished_at = utc_now()
                    task.speed_bytes_per_sec = 0.0
                    self._save(task, "torrent_cancelled")
                    return
                if self.pause_event.is_set():
                    backend.pause()
                    task = self._task()
                    task.status = TaskStatus.PAUSED.value
                    task.speed_bytes_per_sec = 0.0
                    self._save(task, "torrent_paused")
                    return

                snapshot = backend.poll()
                if snapshot.error:
                    raise TorrentError(snapshot.error)
                task = self._task()
                task.status = TaskStatus.RUNNING.value
                task.filename = snapshot.name or task.filename
                task.final_path = snapshot.output_path or task.final_path
                task.total_bytes = snapshot.total_bytes or task.total_bytes
                task.downloaded_bytes = snapshot.downloaded_bytes
                task.speed_bytes_per_sec = snapshot.download_rate
                task.progress_percent = round(max(0.0, min(1.0, snapshot.progress)) * 100, 2)
                task.metadata.update(
                    {
                        "torrent_state": snapshot.state,
                        "metadata_ready": snapshot.metadata_ready,
                        "upload_speed_bytes_per_sec": snapshot.upload_rate,
                        "uploaded_bytes": snapshot.uploaded_bytes,
                        "peers": snapshot.peers,
                        "seeds": snapshot.seeds,
                        "ratio": round(snapshot.ratio, 4),
                        "files": [item.to_dict() for item in snapshot.files],
                    }
                )
                self._save(task)

                if snapshot.finished:
                    if seeding_started == 0:
                        seeding_started = time.monotonic()
                    ratio_target = float(task.metadata.get("seed_ratio") or 0.0)
                    time_target = int(task.metadata.get("seed_time_seconds") or 0)
                    stop_after = bool(task.metadata.get("stop_after_download", False))
                    ratio_met = ratio_target <= 0 or snapshot.ratio >= ratio_target
                    time_met = time_target <= 0 or time.monotonic() - seeding_started >= time_target
                    if stop_after or (ratio_met and time_met):
                        backend.save_resume()
                        task.status = TaskStatus.COMPLETED.value
                        task.finished_at = utc_now()
                        task.progress_percent = 100.0
                        task.speed_bytes_per_sec = 0.0
                        self._save(task, "torrent_completed")
                        return
                    task.metadata["torrent_state"] = "seeding"
                    self._save(task)
                time.sleep(self.poll_interval)
        except Exception as exc:
            task = self._task()
            task.status = TaskStatus.FAILED.value
            task.finished_at = utc_now()
            task.error = str(exc)
            task.error_code = "torrent_failed"
            task.speed_bytes_per_sec = 0.0
            self._save(task, "torrent_failed")
        finally:
            if backend is not None:
                backend.close()
