"""Flask blueprint for Lumi Wave 3 media, torrent and archive APIs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from .wave3 import (
    archive_group,
    archive_info,
    archive_test,
    cancel_post_process,
    media_info,
    run_post_process,
    set_post_process_plan,
    start_torrent,
    start_video,
    torrent_info,
    wave3_capabilities,
)


wave3_blueprint = Blueprint("lumi_wave3", __name__)


def _body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _call(operation: Callable[[], Any]):
    try:
        return jsonify(operation())
    except KeyError as exc:
        return jsonify({"error": f"not found: {exc}"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _target_dir(data: dict[str, Any]) -> Path:
    value = str(data.get("target_dir") or "").strip()
    target = Path(value).expanduser() if value else Path.home() / "Downloads"
    target.mkdir(parents=True, exist_ok=True)
    return target


@wave3_blueprint.get("/api/wave3/capabilities")
def api_wave3_capabilities():
    return jsonify(wave3_capabilities())


@wave3_blueprint.post("/api/media/resolve")
def api_media_resolve():
    data = _body()
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    return _call(
        lambda: media_info(url, playlist=bool(data.get("playlist", True)))
    )


@wave3_blueprint.post("/api/media/start")
def api_media_start():
    data = _body()
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    languages = data.get("subtitle_languages")
    if languages is not None and not isinstance(languages, list):
        return jsonify({"error": "subtitle_languages must be a list"}), 400
    return _call(
        lambda: start_video(
            url,
            target_dir=_target_dir(data),
            format_id=str(data.get("format_id") or "bestvideo+bestaudio/best"),
            audio_only=bool(data.get("audio_only")),
            subtitles=bool(data.get("subtitles")),
            subtitle_languages=[str(item) for item in (languages or [])],
            thumbnail=bool(data.get("thumbnail", True)),
            embed_metadata=bool(data.get("embed_metadata", True)),
            playlist=bool(data.get("playlist")),
            playlist_items=str(data.get("playlist_items") or ""),
            output_container=str(data.get("output_container") or ""),
            audio_format=str(data.get("audio_format") or "mp3"),
            queue_id=str(data.get("queue_id") or "default"),
            priority=int(data.get("priority") or 0),
            start_paused=bool(data.get("start_paused")),
            category_id=str(data.get("category_id") or "video"),
        )
    )


@wave3_blueprint.post("/api/torrents/inspect")
def api_torrent_inspect():
    source = str(_body().get("source") or "").strip()
    if not source:
        return jsonify({"error": "source required"}), 400
    return _call(lambda: torrent_info(source))


@wave3_blueprint.post("/api/torrents/start")
def api_torrent_start():
    data = _body()
    source = str(data.get("source") or data.get("url") or "").strip()
    if not source:
        return jsonify({"error": "source required"}), 400
    selected = data.get("selected_file_indexes")
    priorities = data.get("file_priorities")
    if selected is not None and not isinstance(selected, list):
        return jsonify({"error": "selected_file_indexes must be a list"}), 400
    if priorities is not None and not isinstance(priorities, dict):
        return jsonify({"error": "file_priorities must be an object"}), 400
    return _call(
        lambda: start_torrent(
            source,
            target_dir=_target_dir(data),
            connections=int(data.get("connections") or 0),
            selected_file_indexes=[int(item) for item in (selected or [])],
            file_priorities={
                int(key): int(value)
                for key, value in dict(priorities or {}).items()
            },
            ratio_limit=float(data.get("ratio_limit") or 0),
            seed_time_minutes=int(data.get("seed_time_minutes") or 0),
            upload_limit_bps=int(data.get("upload_limit_bps") or 0),
            download_limit_bps=int(data.get("download_limit_bps") or 0),
            stop_after_download=bool(data.get("stop_after_download", True)),
            queue_id=str(data.get("queue_id") or "default"),
            priority=int(data.get("priority") or 0),
            start_paused=bool(data.get("start_paused")),
            category_id=str(data.get("category_id") or ""),
        )
    )


@wave3_blueprint.post("/api/archives/group")
def api_archive_group():
    path = str(_body().get("path") or "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    return _call(lambda: archive_group(path))


@wave3_blueprint.post("/api/archives/list")
def api_archive_list():
    data = _body()
    path = str(data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    return _call(
        lambda: archive_info(path, password=str(data.get("password") or ""))
    )


@wave3_blueprint.post("/api/archives/test")
def api_archive_test():
    data = _body()
    path = str(data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    return _call(
        lambda: archive_test(path, password=str(data.get("password") or ""))
    )


@wave3_blueprint.put("/api/downloads/<task_id>/post-process")
def api_set_post_process(task_id: str):
    data = _body()
    plan = data.get("plan")
    if not isinstance(plan, dict):
        return jsonify({"error": "plan object required"}), 400
    password = data.get("archive_password")
    return _call(
        lambda: set_post_process_plan(
            task_id,
            plan,
            archive_password=(
                None if password is None else str(password)
            ),
        )
    )


@wave3_blueprint.post("/api/downloads/<task_id>/post-process/run")
def api_run_post_process(task_id: str):
    return _call(lambda: run_post_process(task_id))


@wave3_blueprint.post("/api/downloads/<task_id>/post-process/cancel")
def api_cancel_post_process(task_id: str):
    cancelled = cancel_post_process(task_id)
    return jsonify({"cancelled": cancelled, "id": task_id})
