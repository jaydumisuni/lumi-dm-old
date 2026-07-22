"""Persistent post-processing controller for FFmpeg and 7-Zip jobs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import threading
from typing import Any

from core.v2.models import TaskStatus, utc_now
from core.v2.store import StateStore

from .archive import (
    ArchiveCancelled,
    ArchivePasswordRequired,
    SevenZipEngine,
)
from .executables import find_ffmpeg
from .models import PostProcessJob, PostProcessStatus


class PostProcessError(RuntimeError):
    pass


class PostProcessController:
    def __init__(
        self,
        store: StateStore,
        *,
        archive_engine: SevenZipEngine | None = None,
        ffmpeg_binary: str | None = None,
        max_workers: int = 2,
    ):
        self.store = store
        self.archive_engine = archive_engine or SevenZipEngine()
        self.ffmpeg_binary = ffmpeg_binary or find_ffmpeg()
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="lumi-postprocess",
        )
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            for event in self._cancel.values():
                event.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _task(self, task_id: str):
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def _persist(self, job: PostProcessJob, *, task_status: str | None = None) -> None:
        task = self._task(job.task_id)
        jobs = dict(task.post_process.get("jobs") or {})
        jobs[job.id] = job.to_dict()
        task.post_process["jobs"] = jobs
        task.post_process["active_job_id"] = (
            job.id
            if job.status in {
                PostProcessStatus.QUEUED.value,
                PostProcessStatus.RUNNING.value,
                PostProcessStatus.WAITING_INPUT.value,
                PostProcessStatus.PASSWORD_REQUIRED.value,
            }
            else ""
        )
        if task_status:
            task.status = task_status
        self.store.save_task(task)

    def get_job(self, task_id: str, job_id: str) -> PostProcessJob | None:
        task = self.store.get_task(task_id)
        if task is None:
            return None
        value = dict(task.post_process.get("jobs") or {}).get(job_id)
        return PostProcessJob.from_dict(value) if isinstance(value, dict) else None

    def list_jobs(self, task_id: str) -> list[dict[str, Any]]:
        task = self._task(task_id)
        values = dict(task.post_process.get("jobs") or {}).values()
        jobs = [
            PostProcessJob.from_dict(item)
            for item in values
            if isinstance(item, dict)
        ]
        jobs.sort(key=lambda item: item.created_at, reverse=True)
        return [item.to_dict() for item in jobs]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            event = self._cancel.get(job_id)
        if event is None:
            return False
        event.set()
        return True

    def submit_archive(
        self,
        task_id: str,
        archive_path: Path,
        destination_root: Path,
        *,
        password: str = "",
        delete_archive: bool = False,
    ) -> PostProcessJob:
        self._task(task_id)
        job = PostProcessJob(
            kind="archive_extract",
            task_id=task_id,
            input_paths=[str(Path(archive_path))],
            output_path=str(Path(destination_root)),
            metadata={
                "delete_archive": bool(delete_archive),
                "password_supplied": bool(password),
            },
        )
        event = threading.Event()
        with self._lock:
            self._cancel[job.id] = event
        self._persist(job, task_status=TaskStatus.POST_PROCESSING.value)
        self._pool.submit(
            self._run_archive,
            job,
            Path(archive_path),
            Path(destination_root),
            password,
            bool(delete_archive),
            event,
        )
        return job

    def _run_archive(
        self,
        job: PostProcessJob,
        archive_path: Path,
        destination_root: Path,
        password: str,
        delete_archive: bool,
        cancel_event: threading.Event,
    ) -> None:
        job.status = PostProcessStatus.RUNNING.value
        job.started_at = utc_now()
        self._persist(job, task_status=TaskStatus.POST_PROCESSING.value)

        def progress(percent: float, current: str) -> None:
            job.progress_percent = max(0.0, min(100.0, float(percent)))
            job.current_item = current[-300:]
            self._persist(job, task_status=TaskStatus.POST_PROCESSING.value)

        try:
            result = self.archive_engine.extract(
                archive_path,
                destination_root,
                password=password,
                delete_archive=delete_archive,
                progress=progress,
                cancel_event=cancel_event,
            )
            if result.get("status") == "waiting_input":
                job.status = PostProcessStatus.WAITING_INPUT.value
                job.metadata["parts"] = result.get("parts")
                self._persist(job, task_status=TaskStatus.PAUSED.value)
                return
            job.status = PostProcessStatus.COMPLETED.value
            job.progress_percent = 100.0
            job.output_path = str(result.get("output_path") or job.output_path)
            job.metadata.update(result)
            job.finished_at = utc_now()
            task = self._task(job.task_id)
            task.final_path = job.output_path
            task.status = TaskStatus.COMPLETED.value
            task.finished_at = utc_now()
            self.store.save_task(task)
            self._persist(job)
        except ArchivePasswordRequired as exc:
            job.status = PostProcessStatus.PASSWORD_REQUIRED.value
            job.error = str(exc)
            self._persist(job, task_status=TaskStatus.PAUSED.value)
        except ArchiveCancelled as exc:
            job.status = PostProcessStatus.CANCELLED.value
            job.error = str(exc)
            job.finished_at = utc_now()
            self._persist(job, task_status=TaskStatus.PAUSED.value)
        except Exception as exc:
            job.status = PostProcessStatus.FAILED.value
            job.error = str(exc)
            job.finished_at = utc_now()
            task = self._task(job.task_id)
            task.status = TaskStatus.FAILED.value
            task.error = str(exc)
            task.error_code = "archive_postprocess_failed"
            self.store.save_task(task)
            self._persist(job)
        finally:
            with self._lock:
                self._cancel.pop(job.id, None)

    def submit_ffmpeg(
        self,
        task_id: str,
        input_paths: list[Path],
        output_path: Path,
        *,
        mode: str = "merge",
        duration_seconds: float = 0.0,
        audio_codec: str = "mp3",
    ) -> PostProcessJob:
        if not self.ffmpeg_binary:
            raise PostProcessError("FFmpeg is not available")
        self._task(task_id)
        paths = [Path(item) for item in input_paths]
        if not paths or any(not item.is_file() for item in paths):
            raise FileNotFoundError("One or more FFmpeg inputs are missing")
        if mode not in {"merge", "remux", "audio_extract"}:
            raise ValueError(f"Unsupported FFmpeg mode: {mode}")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        job = PostProcessJob(
            kind=f"ffmpeg_{mode}",
            task_id=task_id,
            input_paths=[str(item) for item in paths],
            output_path=str(output_path),
            metadata={
                "mode": mode,
                "duration_seconds": max(0.0, float(duration_seconds)),
                "audio_codec": audio_codec,
            },
        )
        event = threading.Event()
        with self._lock:
            self._cancel[job.id] = event
        self._persist(job, task_status=TaskStatus.POST_PROCESSING.value)
        self._pool.submit(self._run_ffmpeg, job, paths, output_path, event)
        return job

    def _ffmpeg_command(
        self,
        job: PostProcessJob,
        inputs: list[Path],
        output: Path,
    ) -> list[str]:
        assert self.ffmpeg_binary is not None
        command = [self.ffmpeg_binary, "-y"]
        for item in inputs:
            command.extend(["-i", str(item)])
        mode = str(job.metadata.get("mode") or "merge")
        if mode in {"merge", "remux"}:
            command.extend(["-c", "copy"])
        else:
            codec = str(job.metadata.get("audio_codec") or "mp3")
            codec_map = {
                "mp3": ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
                "aac": ["-vn", "-c:a", "aac", "-b:a", "192k"],
                "flac": ["-vn", "-c:a", "flac"],
                "opus": ["-vn", "-c:a", "libopus", "-b:a", "160k"],
            }
            command.extend(codec_map.get(codec, codec_map["mp3"]))
        command.extend(["-progress", "pipe:1", "-nostats", str(output)])
        return command

    def _run_ffmpeg(
        self,
        job: PostProcessJob,
        inputs: list[Path],
        output: Path,
        cancel_event: threading.Event,
    ) -> None:
        job.status = PostProcessStatus.RUNNING.value
        job.started_at = utc_now()
        self._persist(job, task_status=TaskStatus.POST_PROCESSING.value)
        command = self._ffmpeg_command(job, inputs, output)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        duration = float(job.metadata.get("duration_seconds") or 0.0)
        tail: list[str] = []
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.strip()
                tail.append(line)
                tail = tail[-40:]
                if cancel_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise PostProcessError("FFmpeg job was cancelled")
                if line.startswith("out_time_ms=") and duration > 0:
                    micros = float(line.split("=", 1)[1] or 0)
                    job.progress_percent = min(99.0, micros / 1_000_000 / duration * 100)
                elif line == "progress=end":
                    job.progress_percent = 100.0
                job.current_item = line[-300:]
                self._persist(job, task_status=TaskStatus.POST_PROCESSING.value)
            returncode = process.wait()
            if returncode != 0:
                raise PostProcessError("\n".join(tail[-12:]) or f"FFmpeg exited {returncode}")
            if not output.is_file():
                raise PostProcessError("FFmpeg did not produce the expected output")
            job.status = PostProcessStatus.COMPLETED.value
            job.progress_percent = 100.0
            job.finished_at = utc_now()
            task = self._task(job.task_id)
            task.final_path = str(output)
            task.filename = output.name
            task.status = TaskStatus.COMPLETED.value
            task.finished_at = utc_now()
            self.store.save_task(task)
            self._persist(job)
        except Exception as exc:
            output.unlink(missing_ok=True)
            job.status = (
                PostProcessStatus.CANCELLED.value
                if cancel_event.is_set()
                else PostProcessStatus.FAILED.value
            )
            job.error = str(exc)
            job.finished_at = utc_now()
            task = self._task(job.task_id)
            task.status = (
                TaskStatus.PAUSED.value
                if cancel_event.is_set()
                else TaskStatus.FAILED.value
            )
            task.error = str(exc)
            task.error_code = "ffmpeg_postprocess_failed"
            self.store.save_task(task)
            self._persist(job)
        finally:
            with self._lock:
                self._cancel.pop(job.id, None)
