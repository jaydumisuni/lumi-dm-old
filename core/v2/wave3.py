"""Wave 3 public services for media, torrents, archives and post-processing."""
from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from .archives import ArchiveService, group_multipart
from .media import MediaService
from .models import TaskStatus, TaskType
from .postprocess import PostProcessController
from .runtime import _require_runtime
from .tools import capabilities as tool_capabilities
from .torrents import TorrentService
from .vault import LocalSecretVault


_POST_CONTROLS: dict[str, threading.Event] = {}
_POST_LOCK = threading.RLock()


def media_info(url: str, *, playlist: bool = True) -> dict[str, Any]:
    runtime = _require_runtime()
    return MediaService(runtime.store).resolve(url, playlist=playlist)


def torrent_info(source: str) -> dict[str, Any]:
    runtime = _require_runtime()
    return TorrentService(runtime.store).inspect(source)


def archive_info(path: str, *, password: str = "") -> dict[str, Any]:
    return ArchiveService().list(Path(path), password=password)


def archive_test(path: str, *, password: str = "") -> dict[str, Any]:
    return ArchiveService().test(Path(path), password=password)


def archive_group(path: str) -> dict[str, Any]:
    return group_multipart(Path(path))


def start_video(
    url: str,
    *,
    target_dir: Path,
    format_id: str = "bestvideo+bestaudio/best",
    audio_only: bool = False,
    subtitles: bool = False,
    subtitle_languages: list[str] | None = None,
    thumbnail: bool = True,
    embed_metadata: bool = True,
    playlist: bool = False,
    playlist_items: str = "",
    output_container: str = "",
    audio_format: str = "mp3",
    queue_id: str = "default",
    priority: int = 0,
    start_paused: bool = False,
    category_id: str = "video",
) -> dict[str, Any]:
    from .wave2 import services

    return services().start_delegated(
        TaskType.VIDEO.value,
        url,
        target_dir=target_dir,
        metadata={
            "filename": "Fetching title…",
            "format_id": format_id,
            "audio_only": audio_only,
            "subtitles": subtitles,
            "subtitle_languages": subtitle_languages or [],
            "thumbnail": thumbnail,
            "embed_metadata": embed_metadata,
            "playlist": playlist,
            "playlist_items": playlist_items,
            "output_container": output_container,
            "audio_format": audio_format,
        },
        queue_id=queue_id,
        priority=priority,
        start_paused=start_paused,
        category_id=category_id,
    )


def start_torrent(
    url: str,
    *,
    target_dir: Path,
    connections: int = 0,
    selected_file_indexes: list[int] | None = None,
    file_priorities: dict[int, int] | None = None,
    ratio_limit: float = 0.0,
    seed_time_minutes: int = 0,
    upload_limit_bps: int = 0,
    download_limit_bps: int = 0,
    stop_after_download: bool = True,
    queue_id: str = "default",
    priority: int = 0,
    start_paused: bool = False,
    category_id: str = "",
) -> dict[str, Any]:
    from .wave2 import services

    metadata = {
        "filename": url[:60] if url.startswith("magnet:") else Path(url).name,
        "connections": connections,
        "selected_file_indexes": selected_file_indexes or [],
        "file_priorities": {
            str(key): int(value) for key, value in (file_priorities or {}).items()
        },
        "ratio_limit": ratio_limit,
        "seed_time_minutes": seed_time_minutes,
        "upload_limit_bps": upload_limit_bps,
        "download_limit_bps": download_limit_bps,
        "stop_after_download": stop_after_download,
    }
    try:
        metadata["torrent_info"] = torrent_info(url)
        metadata["filename"] = metadata["torrent_info"].get("name") or metadata["filename"]
    except Exception as exc:
        metadata["metadata_error"] = str(exc)
    return services().start_delegated(
        TaskType.TORRENT.value,
        url,
        target_dir=target_dir,
        metadata=metadata,
        queue_id=queue_id,
        priority=priority,
        start_paused=start_paused,
        category_id=category_id,
    )


def set_post_process_plan(
    task_id: str,
    plan: dict[str, Any],
    *,
    archive_password: str | None = None,
) -> dict[str, Any]:
    runtime = _require_runtime()
    task = runtime.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    value = dict(plan or {})
    if archive_password is not None:
        old_reference = str(value.get("archive_password_reference") or "")
        vault = LocalSecretVault(runtime.data_dir)
        value["archive_password_reference"] = vault.replace(
            old_reference,
            {"password": archive_password},
        ) if archive_password else ""
    task.post_process = value
    runtime.store.save_task(task)
    runtime.store.append_event(
        task.id,
        "post_process_plan_updated",
        {"stages": sorted(value.keys())},
    )
    return task.to_dict(public=True)


def run_post_process(task_id: str) -> dict[str, Any]:
    runtime = _require_runtime()
    task = runtime.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    if task.status not in {
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.PAUSED.value,
    }:
        raise ValueError("Task must be completed, failed or paused before post-processing")
    if not task.post_process:
        raise ValueError("Task has no post-processing plan")
    with _POST_LOCK:
        existing = _POST_CONTROLS.get(task_id)
        if existing is not None:
            raise ValueError("Post-processing is already running")
        cancel_event = threading.Event()
        _POST_CONTROLS[task_id] = cancel_event

    def worker() -> None:
        try:
            current = runtime.get_task(task_id)
            if current is not None:
                PostProcessController(runtime.store).run(
                    current,
                    cancel_event=cancel_event,
                )
        finally:
            with _POST_LOCK:
                _POST_CONTROLS.pop(task_id, None)

    threading.Thread(
        target=worker,
        name=f"lumi-post-{task_id[:8]}",
        daemon=True,
    ).start()
    task.status = TaskStatus.POST_PROCESSING.value
    runtime.store.save_task(task)
    return task.to_dict(public=True)


def cancel_post_process(task_id: str) -> bool:
    with _POST_LOCK:
        event = _POST_CONTROLS.get(task_id)
        if event is None:
            return False
        event.set()
        return True


def wave3_capabilities() -> dict[str, Any]:
    runtime = _require_runtime()
    tools = tool_capabilities()
    return {
        **tools,
        "yt_dlp": MediaService(runtime.store).available,
        "torrent_metadata": True,
        "torrent_file_selection": True,
        "archive_inspection": bool(tools["seven_zip"]),
        "secure_extraction": bool(tools["seven_zip"]),
        "post_processing": True,
    }
