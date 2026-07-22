"""yt-dlp media resolution and transfer service for Lumi DM v2."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any

from .models import DownloadTask, TaskStatus, utc_now
from .store import StateStore
from .tools import find_ffmpeg

try:
    import yt_dlp as _yt_dlp
except ImportError:  # pragma: no cover - exercised through capability checks
    _yt_dlp = None


class MediaUnavailable(RuntimeError):
    pass


class MediaPaused(Exception):
    pass


class MediaCancelled(Exception):
    pass


@dataclass(slots=True)
class MediaSelection:
    format_id: str = "bestvideo+bestaudio/best"
    audio_only: bool = False
    subtitles: bool = False
    subtitle_languages: list[str] | None = None
    thumbnail: bool = True
    metadata: bool = True
    playlist: bool = False
    playlist_items: str = ""
    output_container: str = ""
    audio_format: str = "mp3"

    @classmethod
    def from_task(cls, task: DownloadTask) -> "MediaSelection":
        value = task.metadata
        return cls(
            format_id=str(value.get("format_id") or "bestvideo+bestaudio/best"),
            audio_only=bool(value.get("audio_only")),
            subtitles=bool(value.get("subtitles")),
            subtitle_languages=[
                str(item) for item in list(value.get("subtitle_languages") or [])
            ] or None,
            thumbnail=bool(value.get("thumbnail", True)),
            metadata=bool(value.get("embed_metadata", True)),
            playlist=bool(value.get("playlist")),
            playlist_items=str(value.get("playlist_items") or ""),
            output_container=str(value.get("output_container") or ""),
            audio_format=str(value.get("audio_format") or "mp3"),
        )


class MediaService:
    def __init__(self, store: StateStore):
        self.store = store

    @property
    def available(self) -> bool:
        return _yt_dlp is not None

    def resolve(self, url: str, *, playlist: bool = True) -> dict[str, Any]:
        if _yt_dlp is None:
            raise MediaUnavailable("Video support requires yt-dlp")
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist" if playlist else False,
            "noplaylist": not playlist,
        }
        with _yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=False)
        if not info:
            raise RuntimeError("Could not resolve media information")

        entries = []
        for index, entry in enumerate(info.get("entries") or []):
            if not entry:
                continue
            entries.append(
                {
                    "index": index + 1,
                    "id": entry.get("id"),
                    "title": entry.get("title") or entry.get("id") or f"Item {index + 1}",
                    "url": entry.get("webpage_url") or entry.get("url"),
                    "duration": entry.get("duration"),
                    "thumbnail": entry.get("thumbnail"),
                }
            )

        formats = []
        for item in info.get("formats") or []:
            format_id = str(item.get("format_id") or "")
            if not format_id:
                continue
            formats.append(
                {
                    "format_id": format_id,
                    "label": item.get("format_note") or item.get("resolution") or format_id,
                    "ext": item.get("ext"),
                    "width": item.get("width"),
                    "height": item.get("height"),
                    "fps": item.get("fps"),
                    "vcodec": item.get("vcodec"),
                    "acodec": item.get("acodec"),
                    "filesize": item.get("filesize") or item.get("filesize_approx") or 0,
                    "tbr": item.get("tbr"),
                    "abr": item.get("abr"),
                    "dynamic_range": item.get("dynamic_range"),
                }
            )
        formats.sort(
            key=lambda item: (
                int(item.get("height") or 0),
                float(item.get("tbr") or 0),
            ),
            reverse=True,
        )
        return {
            "url": url,
            "id": info.get("id"),
            "title": info.get("title") or "Media",
            "uploader": info.get("uploader") or info.get("channel"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "description": info.get("description"),
            "is_playlist": bool(entries),
            "entries": entries,
            "formats": formats,
            "subtitles": sorted((info.get("subtitles") or {}).keys()),
            "automatic_captions": sorted(
                (info.get("automatic_captions") or {}).keys()
            ),
            "ffmpeg": bool(find_ffmpeg()),
        }

    def run(
        self,
        task: DownloadTask,
        pause_event: threading.Event,
        cancel_event: threading.Event,
    ) -> DownloadTask:
        if _yt_dlp is None:
            raise MediaUnavailable("Video support requires yt-dlp")
        selection = MediaSelection.from_task(task)
        target = Path(task.target_dir)
        target.mkdir(parents=True, exist_ok=True)
        ffmpeg = find_ffmpeg()

        task.status = TaskStatus.RUNNING.value
        task.started_at = task.started_at or utc_now()
        task.mode = "yt-dlp"
        task.error = ""
        self.store.save_task(task)
        self.store.append_event(task.id, "media_transfer_started")

        def progress_hook(value: dict[str, Any]) -> None:
            if cancel_event.is_set():
                raise MediaCancelled()
            if pause_event.is_set():
                raise MediaPaused()
            status = value.get("status")
            filename = value.get("filename") or value.get("info_dict", {}).get("_filename")
            if filename:
                task.final_path = str(filename)
                task.filename = Path(filename).name
            if status == "downloading":
                task.downloaded_bytes = int(value.get("downloaded_bytes") or 0)
                task.total_bytes = int(
                    value.get("total_bytes")
                    or value.get("total_bytes_estimate")
                    or task.total_bytes
                    or 0
                )
                task.speed_bytes_per_sec = float(value.get("speed") or 0)
                task.progress_percent = (
                    round(task.downloaded_bytes * 100 / task.total_bytes, 2)
                    if task.total_bytes
                    else 0.0
                )
                task.metadata["eta_seconds"] = value.get("eta")
                task.metadata["fragment_index"] = value.get("fragment_index")
                task.metadata["fragment_count"] = value.get("fragment_count")
                self.store.save_task(task)
            elif status == "finished":
                task.status = TaskStatus.POST_PROCESSING.value
                task.progress_percent = 100.0
                task.speed_bytes_per_sec = 0.0
                self.store.save_task(task)
                self.store.append_event(task.id, "media_download_finished")

        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "continuedl": True,
            "retries": 5,
            "fragment_retries": 5,
            "concurrent_fragment_downloads": max(1, min(16, task.connections)),
            "outtmpl": str(target / "%(title).180B [%(id)s].%(ext)s"),
            "progress_hooks": [progress_hook],
            "noplaylist": not selection.playlist,
            "format": (
                "bestaudio/best" if selection.audio_only else selection.format_id
            ),
            "writesubtitles": selection.subtitles,
            "writeautomaticsub": selection.subtitles,
            "subtitleslangs": selection.subtitle_languages or ["all"],
            "writethumbnail": selection.thumbnail,
            "embedthumbnail": bool(selection.thumbnail and ffmpeg),
            "addmetadata": selection.metadata,
            "embedmetadata": bool(selection.metadata and ffmpeg),
        }
        if selection.playlist_items:
            options["playlist_items"] = selection.playlist_items
        if ffmpeg:
            options["ffmpeg_location"] = str(Path(ffmpeg).parent)
        if selection.output_container:
            options["merge_output_format"] = selection.output_container
        if selection.audio_only:
            if not ffmpeg:
                raise MediaUnavailable("Audio conversion requires FFmpeg")
            options["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": selection.audio_format,
                    "preferredquality": "0",
                }
            ]

        try:
            with _yt_dlp.YoutubeDL(options) as downloader:
                result = downloader.extract_info(task.request.url, download=True)
            if result:
                prepared = downloader.prepare_filename(result)
                if selection.audio_only:
                    prepared = str(Path(prepared).with_suffix(f".{selection.audio_format}"))
                task.final_path = prepared
                task.filename = Path(prepared).name
                if Path(prepared).is_file():
                    task.downloaded_bytes = Path(prepared).stat().st_size
                    task.total_bytes = task.downloaded_bytes
            task.status = TaskStatus.COMPLETED.value
            task.progress_percent = 100.0
            task.speed_bytes_per_sec = 0.0
            task.finished_at = utc_now()
            self.store.save_task(task)
            self.store.append_event(task.id, "media_completed")
            return task
        except MediaPaused:
            task.status = TaskStatus.PAUSED.value
            task.speed_bytes_per_sec = 0.0
            self.store.save_task(task)
            self.store.append_event(task.id, "media_paused")
            return task
        except MediaCancelled:
            task.status = TaskStatus.CANCELLED.value
            task.speed_bytes_per_sec = 0.0
            task.finished_at = utc_now()
            self.store.save_task(task)
            self.store.append_event(task.id, "media_cancelled")
            return task
        except Exception as exc:
            task.status = TaskStatus.FAILED.value
            task.error = str(exc)
            task.error_code = "media_failed"
            task.speed_bytes_per_sec = 0.0
            task.finished_at = utc_now()
            self.store.save_task(task)
            self.store.append_event(task.id, "media_failed", {"error": str(exc)})
            return task
