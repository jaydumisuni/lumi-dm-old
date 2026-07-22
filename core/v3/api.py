"""Flask Blueprint exposing Wave 3 application functions."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from .runtime_wave3 import (
    cancel_postprocess,
    extract_archive,
    get_postprocess_job,
    inspect_archive,
    inspect_media,
    inspect_torrent,
    list_postprocess_jobs,
    start_torrent,
    start_video,
    submit_ffmpeg,
    test_archive,
)


wave3_api = Blueprint("lumi_wave3", __name__, url_prefix="/api/v3")


def _body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _status(exc: Exception) -> int:
    if isinstance(exc, KeyError):
        return 404
    if isinstance(exc, FileNotFoundError):
        return 404
    if isinstance(exc, ValueError):
        return 400
    return 503


def _call(operation: Callable[[], Any]):
    try:
        return jsonify(operation())
    except Exception as exc:
        return jsonify({"error": str(exc)}), _status(exc)


@wave3_api.get("/media/info")
def media_info():
    url = str(request.args.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    include_playlist = str(request.args.get("playlist") or "true").lower() != "false"
    return _call(lambda: inspect_media(url, include_playlist=include_playlist))


@wave3_api.post("/media/start")
def media_start():
    data = _body()
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    return _call(
        lambda: start_video(
            url,
            target_dir=Path(str(data.get("target_dir") or Path.home() / "Downloads")),
            format_id=str(data.get("format_id") or "bestvideo+bestaudio/best"),
            audio_only=bool(data.get("audio_only")),
            video_only=bool(data.get("video_only")),
            subtitles=bool(data.get("subtitles")),
            playlist=bool(data.get("playlist")),
            playlist_items=[int(item) for item in data.get("playlist_items") or []],
            subtitle_languages=[str(item) for item in data.get("subtitle_languages") or []],
            automatic_subtitles=bool(data.get("automatic_subtitles", True)),
            embed_subtitles=bool(data.get("embed_subtitles", True)),
            thumbnail=bool(data.get("thumbnail", True)),
            embed_thumbnail=bool(data.get("embed_thumbnail", True)),
            metadata=bool(data.get("metadata", True)),
            audio_format=str(data.get("audio_format") or "mp3"),
            audio_quality=str(data.get("audio_quality") or "192"),
            merge_output_format=str(data.get("merge_output_format") or ""),
            queue_id=str(data.get("queue_id") or "default"),
            priority=int(data.get("priority") or 0),
            start_paused=bool(data.get("start_paused")),
        )
    )


@wave3_api.get("/torrent/info")
def torrent_info():
    source = str(request.args.get("source") or request.args.get("url") or "").strip()
    if not source:
        return jsonify({"error": "source required"}), 400
    return _call(lambda: inspect_torrent(source))


@wave3_api.post("/torrent/start")
def torrent_start():
    data = _body()
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    return _call(
        lambda: start_torrent(
            url,
            target_dir=Path(str(data.get("target_dir") or Path.home() / "Downloads")),
            connections=int(data.get("connections") or 0),
            queue_id=str(data.get("queue_id") or "default"),
            priority=int(data.get("priority") or 0),
            start_paused=bool(data.get("start_paused")),
            selected_files=[int(item) for item in data.get("selected_files") or []],
            file_priorities=[int(item) for item in data.get("file_priorities") or []],
            seed_ratio=float(data.get("seed_ratio") or 0.0),
            seed_time_seconds=int(data.get("seed_time_seconds") or 0),
            stop_after_download=bool(data.get("stop_after_download")),
        )
    )


@wave3_api.post("/archive/inspect")
def archive_inspect():
    data = _body()
    path = str(data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    return _call(
        lambda: inspect_archive(
            Path(path),
            password=str(data.get("password") or ""),
        )
    )


@wave3_api.post("/archive/test")
def archive_test():
    data = _body()
    path = str(data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    return _call(
        lambda: test_archive(
            Path(path),
            password=str(data.get("password") or ""),
        )
    )


@wave3_api.post("/archive/extract")
def archive_extract():
    data = _body()
    path = str(data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    destination = str(data.get("destination_root") or "").strip()
    return _call(
        lambda: extract_archive(
            Path(path),
            task_id=str(data.get("task_id") or ""),
            destination_root=Path(destination) if destination else None,
            password=str(data.get("password") or ""),
            delete_archive=bool(data.get("delete_archive")),
        )
    )


@wave3_api.post("/ffmpeg")
def ffmpeg_submit():
    data = _body()
    task_id = str(data.get("task_id") or "").strip()
    output = str(data.get("output_path") or "").strip()
    inputs = [Path(str(item)) for item in data.get("input_paths") or []]
    if not task_id or not output or not inputs:
        return jsonify({"error": "task_id, input_paths and output_path are required"}), 400
    return _call(
        lambda: submit_ffmpeg(
            task_id,
            inputs,
            Path(output),
            mode=str(data.get("mode") or "merge"),
            duration_seconds=float(data.get("duration_seconds") or 0.0),
            audio_codec=str(data.get("audio_codec") or "mp3"),
        )
    )


@wave3_api.get("/tasks/<task_id>/postprocess")
def postprocess_list(task_id: str):
    return _call(lambda: {"jobs": list_postprocess_jobs(task_id)})


@wave3_api.get("/tasks/<task_id>/postprocess/<job_id>")
def postprocess_get(task_id: str, job_id: str):
    value = get_postprocess_job(task_id, job_id)
    if value is None:
        return jsonify({"error": "post-processing job not found"}), 404
    return jsonify(value)


@wave3_api.post("/postprocess/<job_id>/cancel")
def postprocess_cancel(job_id: str):
    if not cancel_postprocess(job_id):
        return jsonify({"error": "post-processing job not found"}), 404
    return jsonify({"status": "cancelling", "id": job_id})
