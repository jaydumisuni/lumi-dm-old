"""Recoverable post-processing controller for archives and media."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import os
import shutil
import subprocess
import threading
from typing import Any, Callable

from .archives import ArchiveLimits, ArchiveService, is_archive
from .models import DownloadTask, TaskStatus, utc_now
from .store import StateStore
from .tools import find_ffmpeg, find_ffprobe
from .vault import resolve_secret


_ALLOWED_VIDEO_CODECS = {"copy", "libx264", "libx265", "av1", "vp9"}
_ALLOWED_AUDIO_CODECS = {"copy", "aac", "libmp3lame", "flac", "opus", "vorbis"}
_ALLOWED_CONTAINERS = {"mp4", "mkv", "webm", "mov", "mp3", "m4a", "flac", "ogg"}


class PostProcessCancelled(RuntimeError):
    pass


@dataclass(slots=True)
class FFmpegPlan:
    output_container: str
    video_codec: str = "copy"
    audio_codec: str = "copy"
    audio_bitrate: str = ""
    video_quality: str = ""
    delete_source: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FFmpegPlan":
        container = str(value.get("output_container") or "").lower().lstrip(".")
        video = str(value.get("video_codec") or "copy")
        audio = str(value.get("audio_codec") or "copy")
        if container not in _ALLOWED_CONTAINERS:
            raise ValueError(f"Unsupported output container: {container}")
        if video not in _ALLOWED_VIDEO_CODECS:
            raise ValueError(f"Unsupported video codec: {video}")
        if audio not in _ALLOWED_AUDIO_CODECS:
            raise ValueError(f"Unsupported audio codec: {audio}")
        return cls(
            output_container=container,
            video_codec=video,
            audio_codec=audio,
            audio_bitrate=str(value.get("audio_bitrate") or ""),
            video_quality=str(value.get("video_quality") or ""),
            delete_source=bool(value.get("delete_source")),
        )


class FFmpegService:
    def __init__(
        self,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
    ):
        self.ffmpeg = ffmpeg or find_ffmpeg()
        self.ffprobe = ffprobe or find_ffprobe()

    def duration_seconds(self, path: Path) -> float:
        if not self.ffprobe:
            return 0.0
        result = subprocess.run(
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if result.returncode:
            return 0.0
        try:
            return float(json.loads(result.stdout)["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return 0.0

    def convert(
        self,
        source: Path,
        destination: Path,
        plan: FFmpegPlan,
        *,
        cancel_event: threading.Event,
        progress_callback: Callable[[float], None] | None = None,
    ) -> dict[str, Any]:
        if not self.ffmpeg:
            raise RuntimeError("FFmpeg is required for media conversion")
        source = Path(source)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".lumi-processing")
        temporary.unlink(missing_ok=True)
        duration = self.duration_seconds(source)
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map_metadata",
            "0",
            "-c:v",
            plan.video_codec,
            "-c:a",
            plan.audio_codec,
        ]
        if plan.audio_bitrate:
            command.extend(["-b:a", plan.audio_bitrate])
        if plan.video_quality:
            if plan.video_codec in {"libx264", "libx265"}:
                command.extend(["-crf", plan.video_quality])
            elif plan.video_codec in {"av1", "vp9"}:
                command.extend(["-b:v", "0", "-crf", plan.video_quality])
        command.extend(["-progress", "pipe:1", "-nostats", str(temporary)])
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output_tail: list[str] = []
        assert process.stdout is not None
        try:
            for line in process.stdout:
                value = line.strip()
                output_tail.append(value)
                output_tail = output_tail[-100:]
                if cancel_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise PostProcessCancelled("Media conversion cancelled")
                if value.startswith("out_time_ms=") and duration > 0:
                    try:
                        microseconds = int(value.split("=", 1)[1])
                        percent = min(100.0, microseconds / 1_000_000 / duration * 100)
                        if progress_callback:
                            progress_callback(percent)
                    except ValueError:
                        pass
            code = process.wait()
            if code:
                raise RuntimeError("FFmpeg failed: " + " | ".join(output_tail[-20:]))
            os.replace(temporary, destination)
            if plan.delete_source and source.resolve() != destination.resolve():
                source.unlink(missing_ok=True)
            if progress_callback:
                progress_callback(100.0)
            return {
                "status": "completed",
                "source": str(source),
                "destination": str(destination),
                "size": destination.stat().st_size,
            }
        finally:
            temporary.unlink(missing_ok=True)


class PostProcessController:
    def __init__(self, store: StateStore):
        self.store = store
        self.archives = ArchiveService()
        self.ffmpeg = FFmpegService()

    def run(
        self,
        task: DownloadTask,
        *,
        cancel_event: threading.Event,
    ) -> DownloadTask:
        plan = dict(task.post_process or {})
        if not plan:
            return task
        source = Path(task.final_path)
        task.status = TaskStatus.POST_PROCESSING.value
        task.metadata["post_process_stage"] = "starting"
        task.metadata["post_process_progress"] = 0.0
        self.store.save_task(task)
        self.store.append_event(task.id, "post_processing_started")

        try:
            if plan.get("extract") and source.is_file() and is_archive(source):
                password = self._password(plan.get("archive_password_reference"))
                destination = Path(
                    plan.get("extract_destination")
                    or source.with_suffix("")
                )
                task.metadata["post_process_stage"] = "extracting"
                self.store.save_task(task)
                extraction = self.archives.extract(
                    source,
                    destination,
                    password=password,
                    cancel_event=cancel_event,
                    progress_callback=lambda percent: self._progress(task, percent),
                    limits=ArchiveLimits(
                        max_files=int(plan.get("max_archive_files") or 100_000),
                        max_unpacked_bytes=int(
                            plan.get("max_unpacked_bytes")
                            or 250 * 1024 * 1024 * 1024
                        ),
                        max_ratio=float(plan.get("max_archive_ratio") or 1_000),
                    ),
                    delete_source=bool(plan.get("delete_archive")),
                )
                if extraction.get("status") == "waiting_for_parts":
                    task.status = TaskStatus.PAUSED.value
                    task.metadata["post_process_stage"] = "waiting_for_parts"
                    task.metadata["archive_group"] = extraction
                    self.store.save_task(task)
                    return task
                task.metadata["extraction_result"] = extraction

            conversion_value = plan.get("convert")
            if conversion_value:
                conversion = FFmpegPlan.from_dict(dict(conversion_value))
                destination = source.with_suffix(f".{conversion.output_container}")
                if destination == source:
                    destination = source.with_name(
                        f"{source.stem}.converted.{conversion.output_container}"
                    )
                task.metadata["post_process_stage"] = "converting"
                self.store.save_task(task)
                result = self.ffmpeg.convert(
                    source,
                    destination,
                    conversion,
                    cancel_event=cancel_event,
                    progress_callback=lambda percent: self._progress(task, percent),
                )
                task.metadata["conversion_result"] = result
                task.final_path = str(destination)
                task.filename = destination.name

            move_to = str(plan.get("move_to") or "")
            if move_to and Path(task.final_path).exists():
                destination_dir = Path(move_to)
                destination_dir.mkdir(parents=True, exist_ok=True)
                destination = self._unique(destination_dir / Path(task.final_path).name)
                shutil.move(task.final_path, destination)
                task.final_path = str(destination)
                task.filename = destination.name
                task.metadata["post_process_stage"] = "moved"

            task.status = TaskStatus.COMPLETED.value
            task.metadata["post_process_stage"] = "completed"
            task.metadata["post_process_progress"] = 100.0
            task.finished_at = utc_now()
            task.error = ""
            task.error_code = ""
            self.store.save_task(task)
            self.store.append_event(task.id, "post_processing_completed")
            return task
        except PostProcessCancelled as exc:
            task.status = TaskStatus.CANCELLED.value
            task.error = str(exc)
            task.error_code = "post_process_cancelled"
            task.finished_at = utc_now()
            self.store.save_task(task)
            return task
        except Exception as exc:
            task.status = TaskStatus.FAILED.value
            task.error = str(exc)
            task.error_code = "post_process_failed"
            task.finished_at = utc_now()
            self.store.save_task(task)
            self.store.append_event(
                task.id,
                "post_processing_failed",
                {"error": str(exc)},
            )
            return task

    def _progress(self, task: DownloadTask, percent: float) -> None:
        task.metadata["post_process_progress"] = round(
            max(0.0, min(100.0, float(percent))),
            2,
        )
        self.store.save_task(task)

    @staticmethod
    def _password(reference: Any) -> str:
        if not reference:
            return ""
        return str(resolve_secret(str(reference)).get("password") or "")

    @staticmethod
    def _unique(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        index = 2
        candidate = path
        while candidate.exists():
            candidate = path.with_name(f"{stem} ({index}){suffix}")
            index += 1
        return candidate
