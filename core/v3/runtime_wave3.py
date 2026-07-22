"""Wave 3 activation for media, torrents, archives and post-processing."""
from __future__ import annotations

from pathlib import Path
import threading
from typing import Any
import uuid

from core.v2 import runtime as _runtime
from core.v2 import runtime_wave2 as _runtime_wave2  # activate secure HTTP replay
from core.v2.models import DownloadTask, RequestEnvelope, TaskStatus, TaskType, utc_now
from core.v2.wave2 import services as wave2_services

from .archive import SevenZipEngine
from .media import MediaInspector, MediaRunner
from .postprocess import PostProcessController
from .torrent import TorrentInspector, TorrentRunner


_CONTROLLERS: dict[int, PostProcessController] = {}
_CONTROLLERS_LOCK = threading.RLock()


def _controller(store) -> PostProcessController:
    key = id(store)
    with _CONTROLLERS_LOCK:
        current = _CONTROLLERS.get(key)
        if current is None:
            current = PostProcessController(store)
            _CONTROLLERS[key] = current
        return current


_BaseHTTPTransferRunner = _runtime.HTTPTransferRunner


class Wave3HTTPTransferRunner(_BaseHTTPTransferRunner):
    """Secure Wave 2 HTTP runner plus automatic archive post-processing."""

    def _complete_file(self, task, partial: Path, final: Path) -> None:
        super()._complete_file(task, partial, final)
        completed = self.store.get_task(task.id)
        if completed is None or not completed.post_process.get("extract"):
            return
        completed.status = TaskStatus.POST_PROCESSING.value
        completed.post_process["archive_source"] = str(final)
        self.store.save_task(completed)
        _controller(self.store).submit_archive(
            completed.id,
            final,
            Path(completed.target_dir),
            delete_archive=bool(completed.post_process.get("delete_archive")),
        )


_runtime.HTTPTransferRunner = Wave3HTTPTransferRunner


if not getattr(_runtime.LumiRuntime, "_lumi_wave3_backend", False):
    _original_legacy_backend = _runtime.LumiRuntime._run_legacy_backend
    _original_completion = _runtime.LumiRuntime._maybe_completion_action

    def _wave3_backend(
        self: _runtime.LumiRuntime,
        task: DownloadTask,
        pause_event: threading.Event,
        cancel_event: threading.Event,
    ) -> None:
        if task.type == TaskType.VIDEO.value:
            MediaRunner(
                self.store,
                task.id,
                pause_event=pause_event,
                cancel_event=cancel_event,
            ).run()
            return
        if task.type == TaskType.TORRENT.value:
            TorrentRunner(
                self.store,
                self.data_dir,
                task.id,
                pause_event=pause_event,
                cancel_event=cancel_event,
            ).run()
            return
        _original_legacy_backend(self, task, pause_event, cancel_event)

    def _wave3_completion(self: _runtime.LumiRuntime) -> None:
        active = self.store.list_tasks(
            statuses={
                TaskStatus.QUEUED.value,
                TaskStatus.RESOLVING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.PAUSING.value,
                TaskStatus.POST_PROCESSING.value,
            },
            limit=1,
        )
        if active:
            return
        _original_completion(self)

    _runtime.LumiRuntime._run_legacy_backend = _wave3_backend
    _runtime.LumiRuntime._maybe_completion_action = _wave3_completion
    _runtime.LumiRuntime._lumi_wave3_backend = True


from core.v2.runtime_wave2 import *  # noqa: E402,F401,F403


def _runtime_instance() -> _runtime.LumiRuntime:
    return _runtime._require_runtime()


def get_capabilities() -> dict[str, Any]:
    capabilities = _runtime.get_capabilities()
    capabilities.update(
        {
            "media_v3": True,
            "playlist_selection": True,
            "torrent_file_selection": True,
            "torrent_seeding_controls": True,
            "archive_7zip": SevenZipEngine().available,
            "archive_secure_extract": True,
            "post_processing": True,
        }
    )
    return capabilities


def inspect_media(url: str, *, include_playlist: bool = True) -> dict[str, Any]:
    return MediaInspector().inspect(url, include_playlist=include_playlist)


def start_video(
    url: str,
    *,
    target_dir: Path,
    format_id: str = "bestvideo+bestaudio/best",
    audio_only: bool = False,
    subtitles: bool = False,
    queue_id: str = "default",
    priority: int = 0,
    start_paused: bool = False,
    category_id: str = "video",
    playlist: bool = False,
    playlist_items: list[int] | None = None,
    subtitle_languages: list[str] | None = None,
    automatic_subtitles: bool = True,
    embed_subtitles: bool = True,
    thumbnail: bool = True,
    embed_thumbnail: bool = True,
    metadata: bool = True,
    video_only: bool = False,
    audio_format: str = "mp3",
    audio_quality: str = "192",
    merge_output_format: str = "",
) -> dict[str, Any]:
    result = wave2_services().start_delegated(
        TaskType.VIDEO.value,
        url,
        target_dir=Path(target_dir),
        metadata={
            "filename": "Fetching title…",
            "format_id": format_id,
            "audio_only": bool(audio_only),
            "video_only": bool(video_only),
            "subtitles": bool(subtitles),
            "playlist": bool(playlist),
            "playlist_items": list(playlist_items or []),
            "subtitle_languages": list(subtitle_languages or []),
            "automatic_subtitles": bool(automatic_subtitles),
            "embed_subtitles": bool(embed_subtitles),
            "thumbnail": bool(thumbnail),
            "embed_thumbnail": bool(embed_thumbnail),
            "metadata": bool(metadata),
            "audio_format": audio_format,
            "audio_quality": audio_quality,
            "merge_output_format": merge_output_format,
        },
        queue_id=queue_id,
        priority=priority,
        start_paused=start_paused,
        category_id=category_id,
    )
    return result


def get_video_formats(url: str) -> dict[str, Any]:
    return inspect_media(url, include_playlist=False)


def inspect_torrent(source: str) -> dict[str, Any]:
    return TorrentInspector().inspect(source)


def start_torrent(
    url: str,
    *,
    target_dir: Path,
    connections: int = 0,
    queue_id: str = "default",
    priority: int = 0,
    start_paused: bool = False,
    category_id: str = "",
    selected_files: list[int] | None = None,
    file_priorities: list[int] | None = None,
    seed_ratio: float = 0.0,
    seed_time_seconds: int = 0,
    stop_after_download: bool = False,
) -> dict[str, Any]:
    return wave2_services().start_delegated(
        TaskType.TORRENT.value,
        url,
        target_dir=Path(target_dir),
        metadata={
            "filename": url[:80] if url.startswith("magnet:") else Path(url).name,
            "connections": int(connections or 0),
            "selected_files": list(selected_files or []),
            "file_priorities": list(file_priorities or []),
            "seed_ratio": max(0.0, float(seed_ratio)),
            "seed_time_seconds": max(0, int(seed_time_seconds)),
            "stop_after_download": bool(stop_after_download),
        },
        queue_id=queue_id,
        priority=priority,
        start_paused=start_paused,
        category_id=category_id,
    )


def create_archive_task(path: Path, *, target_dir: Path | None = None) -> dict[str, Any]:
    runtime = _runtime_instance()
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    destination = Path(target_dir or source.parent)
    destination.mkdir(parents=True, exist_ok=True)
    task = DownloadTask(
        id=uuid.uuid4().hex,
        type="archive",
        status=TaskStatus.PAUSED.value,
        request=RequestEnvelope(url=source.resolve().as_uri()),
        filename=source.name,
        target_dir=str(destination),
        temp_dir=str(destination),
        final_path=str(source),
        partial_path="",
        downloaded_bytes=source.stat().st_size,
        total_bytes=source.stat().st_size,
        progress_percent=100.0,
        metadata={"local_archive": True},
    )
    runtime.store.save_task(task)
    runtime.store.append_event(task.id, "archive_task_created")
    return task.to_dict(public=True)


def inspect_archive(path: Path, *, password: str = "") -> dict[str, Any]:
    return SevenZipEngine().inspect(Path(path), password=password)


def test_archive(path: Path, *, password: str = "") -> dict[str, Any]:
    return SevenZipEngine().test(Path(path), password=password)


def extract_archive(
    path: Path,
    *,
    task_id: str = "",
    destination_root: Path | None = None,
    password: str = "",
    delete_archive: bool = False,
) -> dict[str, Any]:
    runtime = _runtime_instance()
    source = Path(path)
    if not task_id:
        task_id = create_archive_task(
            source,
            target_dir=destination_root or source.parent,
        )["id"]
    task = runtime.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    job = _controller(runtime.store).submit_archive(
        task_id,
        source,
        Path(destination_root or task.target_dir),
        password=password,
        delete_archive=delete_archive,
    )
    return job.to_dict()


def submit_ffmpeg(
    task_id: str,
    input_paths: list[Path],
    output_path: Path,
    *,
    mode: str = "merge",
    duration_seconds: float = 0.0,
    audio_codec: str = "mp3",
) -> dict[str, Any]:
    runtime = _runtime_instance()
    return _controller(runtime.store).submit_ffmpeg(
        task_id,
        input_paths,
        output_path,
        mode=mode,
        duration_seconds=duration_seconds,
        audio_codec=audio_codec,
    ).to_dict()


def list_postprocess_jobs(task_id: str) -> list[dict[str, Any]]:
    runtime = _runtime_instance()
    return _controller(runtime.store).list_jobs(task_id)


def get_postprocess_job(task_id: str, job_id: str) -> dict[str, Any] | None:
    runtime = _runtime_instance()
    job = _controller(runtime.store).get_job(task_id, job_id)
    return job.to_dict() if job else None


def cancel_postprocess(job_id: str) -> bool:
    runtime = _runtime_instance()
    return _controller(runtime.store).cancel(job_id)
