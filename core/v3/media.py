"""yt-dlp media inspection and source-runnable download execution."""
from __future__ import annotations

from pathlib import Path
import threading
from typing import Any, Callable

from core.v2.models import DownloadTask, TaskStatus, utc_now
from core.v2.store import StateStore

from .executables import find_ffmpeg


try:
    import yt_dlp
except ImportError:  # pragma: no cover - capability path
    yt_dlp = None


class MediaError(RuntimeError):
    pass


class MediaPaused(MediaError):
    pass


class MediaCancelled(MediaError):
    pass


def _format_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "format_id": str(item.get("format_id") or ""),
        "format": str(item.get("format") or ""),
        "ext": str(item.get("ext") or ""),
        "filesize": int(item.get("filesize") or item.get("filesize_approx") or 0),
        "width": int(item.get("width") or 0),
        "height": int(item.get("height") or 0),
        "fps": float(item.get("fps") or 0),
        "vcodec": str(item.get("vcodec") or "none"),
        "acodec": str(item.get("acodec") or "none"),
        "abr": float(item.get("abr") or 0),
        "vbr": float(item.get("vbr") or 0),
        "tbr": float(item.get("tbr") or 0),
        "protocol": str(item.get("protocol") or ""),
        "dynamic_range": str(item.get("dynamic_range") or ""),
        "format_note": str(item.get("format_note") or ""),
        "language": str(item.get("language") or ""),
    }


def _subtitle_rows(info: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    for source_name in ("subtitles", "automatic_captions"):
        source = dict(info.get(source_name) or {})
        for language, rows in source.items():
            current = result.setdefault(str(language), [])
            for row in rows or []:
                current.append(
                    {
                        "ext": str(row.get("ext") or ""),
                        "name": str(row.get("name") or ""),
                        "source": source_name,
                    }
                )
    return result


class MediaInspector:
    def __init__(self, ydl_factory: Callable[[dict[str, Any]], Any] | None = None):
        if ydl_factory is None:
            if yt_dlp is None:
                raise MediaError("yt-dlp is not installed")
            ydl_factory = yt_dlp.YoutubeDL
        self.ydl_factory = ydl_factory

    def inspect(self, url: str, *, include_playlist: bool = True) -> dict[str, Any]:
        cleaned = str(url or "").strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("Media URL must be http or https")
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": not include_playlist,
            "extract_flat": "in_playlist" if include_playlist else False,
        }
        with self.ydl_factory(options) as ydl:
            info = ydl.extract_info(cleaned, download=False)
        if not info:
            raise MediaError("yt-dlp returned no media information")

        entries = []
        for index, entry in enumerate(info.get("entries") or [], start=1):
            if not entry:
                continue
            entries.append(
                {
                    "index": index,
                    "id": str(entry.get("id") or ""),
                    "title": str(entry.get("title") or entry.get("id") or "Untitled"),
                    "url": str(entry.get("webpage_url") or entry.get("url") or ""),
                    "duration": float(entry.get("duration") or 0),
                    "uploader": str(entry.get("uploader") or ""),
                    "thumbnail": str(entry.get("thumbnail") or ""),
                }
            )

        formats = [
            _format_row(item)
            for item in info.get("formats") or []
            if item.get("format_id")
        ]
        formats.sort(
            key=lambda row: (
                row["height"],
                row["fps"],
                row["tbr"],
                row["filesize"],
            ),
            reverse=True,
        )
        thumbnails = [
            {
                "url": str(item.get("url") or ""),
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
                "id": str(item.get("id") or ""),
            }
            for item in info.get("thumbnails") or []
            if item.get("url")
        ]
        return {
            "source_type": "playlist" if entries else "media",
            "id": str(info.get("id") or ""),
            "title": str(info.get("title") or "Media"),
            "webpage_url": str(info.get("webpage_url") or cleaned),
            "duration": float(info.get("duration") or 0),
            "uploader": str(info.get("uploader") or ""),
            "description": str(info.get("description") or ""),
            "thumbnail": str(info.get("thumbnail") or ""),
            "thumbnails": thumbnails,
            "entries": entries,
            "formats": formats,
            "subtitles": _subtitle_rows(info),
            "ffmpeg": bool(find_ffmpeg()),
        }


class MediaRunner:
    def __init__(
        self,
        store: StateStore,
        task_id: str,
        *,
        pause_event: threading.Event,
        cancel_event: threading.Event,
        ydl_factory: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.store = store
        self.task_id = task_id
        self.pause_event = pause_event
        self.cancel_event = cancel_event
        if ydl_factory is None:
            if yt_dlp is None:
                raise MediaError("yt-dlp is not installed")
            ydl_factory = yt_dlp.YoutubeDL
        self.ydl_factory = ydl_factory
        self._control_error: Exception | None = None

    def _task(self) -> DownloadTask:
        task = self.store.get_task(self.task_id)
        if task is None:
            raise KeyError(self.task_id)
        return task

    def _save(self, task: DownloadTask, event: str = "") -> None:
        self.store.save_task(task)
        if event:
            self.store.append_event(task.id, event)

    def _check_control(self) -> None:
        if self.cancel_event.is_set():
            self._control_error = MediaCancelled("Media download was cancelled")
            raise self._control_error
        if self.pause_event.is_set():
            self._control_error = MediaPaused("Media download was paused")
            raise self._control_error

    def _progress_hook(self, data: dict[str, Any]) -> None:
        self._check_control()
        task = self._task()
        status = str(data.get("status") or "")
        info = dict(data.get("info_dict") or {})
        filename = str(data.get("filename") or info.get("_filename") or "")
        if filename:
            task.partial_path = filename
            task.filename = Path(filename).name
        if status == "downloading":
            task.status = TaskStatus.RUNNING.value
            task.downloaded_bytes = int(data.get("downloaded_bytes") or 0)
            task.total_bytes = int(
                data.get("total_bytes")
                or data.get("total_bytes_estimate")
                or task.total_bytes
                or 0
            )
            task.speed_bytes_per_sec = float(data.get("speed") or 0.0)
            task.progress_percent = (
                round(task.downloaded_bytes * 100 / task.total_bytes, 2)
                if task.total_bytes
                else 0.0
            )
            task.metadata["eta_seconds"] = int(data.get("eta") or 0)
            task.metadata["fragment_index"] = int(data.get("fragment_index") or 0)
            task.metadata["fragment_count"] = int(data.get("fragment_count") or 0)
            task.metadata["playlist_index"] = int(info.get("playlist_index") or 0)
            task.metadata["current_title"] = str(info.get("title") or "")
            self._save(task)
        elif status == "finished":
            task.status = TaskStatus.POST_PROCESSING.value
            task.speed_bytes_per_sec = 0.0
            task.progress_percent = 100.0
            self._save(task, "media_download_finished")
        elif status == "error":
            task.status = TaskStatus.FAILED.value
            task.error = str(data.get("error") or "yt-dlp download failed")
            task.error_code = "media_download_failed"
            self._save(task, "media_download_failed")

    def _postprocessor_hook(self, data: dict[str, Any]) -> None:
        self._check_control()
        task = self._task()
        task.status = TaskStatus.POST_PROCESSING.value
        task.metadata["postprocessor"] = str(data.get("postprocessor") or "FFmpeg")
        task.metadata["postprocessor_status"] = str(data.get("status") or "")
        self._save(task)

    def _options(self, task: DownloadTask) -> dict[str, Any]:
        metadata = task.metadata
        playlist = bool(metadata.get("playlist"))
        output_template = (
            "%(playlist_index)03d - %(title).180B [%(id)s].%(ext)s"
            if playlist
            else "%(title).180B [%(id)s].%(ext)s"
        )
        format_id = str(metadata.get("format_id") or "bestvideo+bestaudio/best")
        if metadata.get("audio_only"):
            format_id = "bestaudio/best"
        elif metadata.get("video_only"):
            format_id = str(metadata.get("format_id") or "bestvideo/bestvideo*")

        options: dict[str, Any] = {
            "format": format_id,
            "outtmpl": str(Path(task.target_dir) / output_template),
            "noplaylist": not playlist,
            "continuedl": True,
            "overwrites": False,
            "nopart": False,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "retries": 5,
            "fragment_retries": 5,
            "concurrent_fragment_downloads": max(1, min(16, task.connections)),
        }
        playlist_items = metadata.get("playlist_items") or []
        if playlist_items:
            options["playlist_items"] = ",".join(str(item) for item in playlist_items)
        if metadata.get("subtitles"):
            options["writesubtitles"] = True
            options["writeautomaticsub"] = bool(metadata.get("automatic_subtitles", True))
            languages = metadata.get("subtitle_languages") or ["all", "-live_chat"]
            options["subtitleslangs"] = [str(item) for item in languages]
            options["embedsubtitles"] = bool(metadata.get("embed_subtitles", True))
        if metadata.get("thumbnail"):
            options["writethumbnail"] = True
            options["embedthumbnail"] = bool(metadata.get("embed_thumbnail", True))
        if metadata.get("metadata", True):
            options["addmetadata"] = True
        if metadata.get("merge_output_format"):
            options["merge_output_format"] = str(metadata["merge_output_format"])
        ffmpeg = find_ffmpeg()
        if ffmpeg:
            options["ffmpeg_location"] = str(Path(ffmpeg).parent)
        postprocessors: list[dict[str, Any]] = []
        if metadata.get("audio_only"):
            postprocessors.append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": str(metadata.get("audio_format") or "mp3"),
                    "preferredquality": str(metadata.get("audio_quality") or "192"),
                }
            )
        if metadata.get("metadata", True):
            postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})
        if metadata.get("thumbnail") and metadata.get("embed_thumbnail", True):
            postprocessors.append({"key": "EmbedThumbnail"})
        if postprocessors:
            options["postprocessors"] = postprocessors
        return options

    @staticmethod
    def _final_paths(info: dict[str, Any]) -> list[Path]:
        paths: list[Path] = []
        entries = info.get("entries") or [info]
        for entry in entries:
            if not entry:
                continue
            candidates = []
            candidates.extend(entry.get("requested_downloads") or [])
            candidates.append(entry)
            for candidate in candidates:
                value = candidate.get("filepath") or candidate.get("_filename")
                if value:
                    path = Path(value)
                    if path.is_file() and path not in paths:
                        paths.append(path)
        return paths

    def run(self) -> None:
        task = self._task()
        task.status = TaskStatus.RESOLVING.value
        task.started_at = task.started_at or utc_now()
        self._save(task, "media_resolving")
        try:
            with self.ydl_factory(self._options(task)) as ydl:
                info = ydl.extract_info(task.request.url, download=True)
            self._check_control()
            if not info:
                raise MediaError("yt-dlp returned no completed media information")
            paths = self._final_paths(info)
            task = self._task()
            task.status = TaskStatus.COMPLETED.value
            task.finished_at = utc_now()
            task.speed_bytes_per_sec = 0.0
            task.progress_percent = 100.0
            task.error = ""
            task.error_code = ""
            task.metadata["media_id"] = str(info.get("id") or "")
            task.metadata["title"] = str(info.get("title") or task.filename)
            task.metadata["playlist_count"] = len(info.get("entries") or [])
            task.metadata["output_files"] = [str(item) for item in paths]
            if paths:
                task.final_path = str(paths[0] if len(paths) == 1 else Path(task.target_dir))
                task.filename = paths[0].name if len(paths) == 1 else str(info.get("title") or "Playlist")
                task.downloaded_bytes = sum(item.stat().st_size for item in paths)
                task.total_bytes = max(task.total_bytes, task.downloaded_bytes)
            self._save(task, "media_completed")
        except Exception as exc:
            task = self._task()
            if self.cancel_event.is_set() or isinstance(self._control_error, MediaCancelled):
                task.status = TaskStatus.CANCELLED.value
                task.error = ""
                task.error_code = ""
                task.finished_at = utc_now()
                self._save(task, "media_cancelled")
            elif self.pause_event.is_set() or isinstance(self._control_error, MediaPaused):
                task.status = TaskStatus.PAUSED.value
                task.error = ""
                task.error_code = ""
                self._save(task, "media_paused")
            else:
                task.status = TaskStatus.FAILED.value
                task.finished_at = utc_now()
                task.error = str(exc)
                task.error_code = "media_failed"
                self._save(task, "media_failed")
