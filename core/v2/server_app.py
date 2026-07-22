"""Lumi DM source-runtime Flask application.

This is application code, not packaging code. A fresh source checkout runs the same
API contract that a later Builder-packaged executable will expose.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from flask import Flask, jsonify, request, send_from_directory

from core.engine_v2 import (
    cancel_all,
    cancel_job,
    clear_done,
    clear_repair_wait,
    confirm_staged,
    create_queue,
    delete_category,
    delete_host_profile,
    delete_job,
    delete_queue,
    get_capabilities,
    get_completion_action,
    get_default_connections,
    get_job,
    get_repair_wait,
    get_video_formats,
    host_intercept_mode,
    list_categories,
    list_host_profiles,
    list_jobs,
    list_queues,
    load_state,
    move_task_to_queue,
    pause_all,
    pause_job,
    repair_download_link,
    repair_from_capture,
    resolve_source,
    resume_all,
    resume_job,
    retry_job,
    save_category,
    save_host_profile,
    set_completion_action,
    set_default_connections,
    set_max_concurrent,
    set_repair_wait,
    set_task_priority,
    stage_download,
    start_http,
    start_torrent,
    start_video,
    task_events,
    update_queue,
    verify_checksum,
)
from core.grabber import crawl_pages, grab_links


_FROZEN = bool(getattr(sys, "frozen", False))
_BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(
    os.environ.get(
        "LUMIDM_STATIC_DIR",
        str(_BUNDLE_ROOT / "static" if _FROZEN else _PROJECT_ROOT / "static"),
    )
)
DATA_DIR = Path(
    os.environ.get(
        "LUMIDM_DATA_DIR",
        str(
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LumiDM"
            if _FROZEN
            else _PROJECT_ROOT / ".lumi-data"
        ),
    )
)
DATA_DIR.mkdir(parents=True, exist_ok=True)
PERSIST_FILE = DATA_DIR / "downloads.json"
SETTINGS_FILE = DATA_DIR / "settings.json"


def _read_settings() -> dict[str, Any]:
    try:
        value = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_settings(value: dict[str, Any]) -> None:
    temporary = SETTINGS_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, SETTINGS_FILE)


_settings = _read_settings()
DEFAULT_DIR = Path(
    _settings.get(
        "default_dir",
        os.environ.get("LUMIDM_DOWNLOAD_DIR", str(Path.home() / "Downloads")),
    )
)
TEMP_DIR = Path(
    _settings.get(
        "temp_dir",
        os.environ.get("LUMIDM_TEMP_DIR", str(DATA_DIR / "temporary")),
    )
)
DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

load_state(PERSIST_FILE)
if _settings.get("max_concurrent"):
    set_max_concurrent(int(_settings["max_concurrent"]))
if _settings.get("default_connections"):
    set_default_connections(int(_settings["default_connections"]))
if _settings.get("completion_action"):
    set_completion_action(str(_settings["completion_action"]))


app = Flask(__name__, static_folder=None)
app.config["JSON_SORT_KEYS"] = False


try:
    import psutil  # type: ignore
except ImportError:
    psutil = None

_net_stats = {"rx_bps": 0, "tx_bps": 0, "capacity_bps": 0, "available": False}
_net_lock = threading.Lock()


def _network_monitor() -> None:
    if psutil is None:
        return
    try:
        previous = psutil.net_io_counters()
        previous_time = time.monotonic()
    except Exception:
        return
    with _net_lock:
        _net_stats["available"] = True
    while True:
        time.sleep(1)
        try:
            current = psutil.net_io_counters()
            current_time = time.monotonic()
            elapsed = max(0.001, current_time - previous_time)
            with _net_lock:
                _net_stats["rx_bps"] = int(
                    (current.bytes_recv - previous.bytes_recv) / elapsed
                )
                _net_stats["tx_bps"] = int(
                    (current.bytes_sent - previous.bytes_sent) / elapsed
                )
            previous = current
            previous_time = current_time
        except Exception:
            continue


if psutil is not None:
    threading.Thread(
        target=_network_monitor,
        name="lumi-network-monitor",
        daemon=True,
    ).start()


def _json_body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _target_dir(data: dict[str, Any]) -> Path:
    value = str(data.get("target_dir") or "").strip()
    return Path(value) if value else DEFAULT_DIR


def _temp_dir(data: dict[str, Any]) -> Path:
    value = str(data.get("temp_dir") or "").strip()
    return Path(value) if value else TEMP_DIR


def _int_arg(name: str, default: int) -> int:
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


def _error_status(exc: Exception) -> int:
    if isinstance(exc, KeyError):
        return 404
    if isinstance(exc, FileExistsError):
        return 409
    if isinstance(exc, ValueError):
        return 400
    return 500


def _json_call(operation: Callable[[], Any]):
    try:
        return jsonify(operation())
    except Exception as exc:
        return jsonify({"error": str(exc)}), _error_status(exc)


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/static/<path:filename>")
def static_files(filename: str):
    return send_from_directory(STATIC_DIR, filename)


@app.get("/api/capabilities")
def api_capabilities():
    capabilities = get_capabilities()
    capabilities.update(
        {
            "categories": True,
            "host_profiles": True,
            "resource_resolver": True,
            "secure_request_capture": True,
            "browser_repair_capture": True,
        }
    )
    return jsonify(capabilities)


@app.get("/api/netstats")
def api_netstats():
    with _net_lock:
        return jsonify(dict(_net_stats))


@app.get("/api/speedtest")
def api_speedtest():
    try:
        import requests

        started = time.monotonic()
        total = 0
        with requests.get(
            "https://speed.cloudflare.com/__down?bytes=5000000",
            stream=True,
            timeout=(10, 20),
            headers={"User-Agent": "Lumi-DM/2.0"},
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_content(64 * 1024):
                total += len(chunk)
        elapsed = max(0.001, time.monotonic() - started)
        bps = int(total / elapsed)
        with _net_lock:
            _net_stats["capacity_bps"] = bps
        return jsonify(
            {
                "bps": bps,
                "mbps": round(bps * 8 / 1_000_000, 2),
                "bytes_downloaded": total,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503


@app.get("/api/downloads")
def api_list_downloads():
    return jsonify({"downloads": list_jobs(_int_arg("limit", 50))})


@app.post("/api/downloads/start")
def api_start_download():
    data = _json_body()
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    return _json_call(
        lambda: start_http(
            url,
            target_dir=_target_dir(data),
            temp_dir=_temp_dir(data),
            filename=str(data.get("filename") or "").strip(),
            overwrite=bool(data.get("overwrite")),
            resume=bool(data.get("resume", True)),
            connections=int(data.get("connections") or 0),
            max_speed_bps=int(data.get("max_speed_bps") or 0),
            queue_id=str(data.get("queue_id") or "default"),
            priority=int(data.get("priority") or 0),
            start_paused=bool(data.get("start_paused")),
            request_envelope=data.get("request_envelope"),
            duplicate_policy=str(data.get("duplicate_policy") or ""),
            category_id=str(data.get("category_id") or ""),
        )
    )


@app.post("/api/downloads/torrent")
def api_start_torrent():
    data = _json_body()
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    return _json_call(
        lambda: start_torrent(
            url,
            target_dir=_target_dir(data),
            connections=int(data.get("connections") or 0),
            queue_id=str(data.get("queue_id") or "default"),
            priority=int(data.get("priority") or 0),
            start_paused=bool(data.get("start_paused")),
            category_id=str(data.get("category_id") or ""),
        )
    )


@app.get("/api/downloads/video/formats")
def api_video_formats():
    url = str(request.args.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    try:
        return jsonify(get_video_formats(url))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503


@app.post("/api/downloads/video")
def api_start_video():
    data = _json_body()
    url = str(data.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "url must be http/https"}), 400
    return _json_call(
        lambda: start_video(
            url,
            target_dir=_target_dir(data),
            format_id=str(data.get("format_id") or "bestvideo+bestaudio/best"),
            audio_only=bool(data.get("audio_only")),
            subtitles=bool(data.get("subtitles")),
            queue_id=str(data.get("queue_id") or "default"),
            priority=int(data.get("priority") or 0),
            start_paused=bool(data.get("start_paused")),
            category_id=str(data.get("category_id") or "video"),
        )
    )


@app.get("/api/downloads/<task_id>")
def api_task(task_id: str):
    task = get_job(task_id)
    return jsonify(task), (404 if task.get("status") == "unknown" else 200)


@app.post("/api/downloads/<task_id>/pause")
def api_pause(task_id: str):
    return jsonify(pause_job(task_id))


@app.post("/api/downloads/<task_id>/resume")
def api_resume(task_id: str):
    return jsonify(resume_job(task_id))


@app.post("/api/downloads/<task_id>/retry")
def api_retry(task_id: str):
    return jsonify(retry_job(task_id))


@app.post("/api/downloads/<task_id>/cancel")
def api_cancel(task_id: str):
    return jsonify(cancel_job(task_id))


@app.post("/api/downloads/<task_id>/delete")
def api_delete(task_id: str):
    return jsonify(
        delete_job(
            task_id,
            delete_file=bool(_json_body().get("delete_file")),
        )
    )


@app.post("/api/downloads/<task_id>/verify")
def api_verify(task_id: str):
    data = _json_body()
    expected = str(data.get("hash") or "").strip()
    if not expected:
        return jsonify({"error": "hash required"}), 400
    return jsonify(
        verify_checksum(task_id, expected, str(data.get("algo") or "sha256"))
    )


@app.post("/api/downloads/<task_id>/repair-link")
def api_repair_link(task_id: str):
    data = _json_body()
    envelope = data.get("request_envelope") or data
    return _json_call(lambda: repair_download_link(task_id, envelope))


@app.post("/api/downloads/<task_id>/repair-wait")
def api_repair_wait(task_id: str):
    data = _json_body()
    return _json_call(
        lambda: set_repair_wait(
            task_id,
            original_page=str(data.get("original_page") or ""),
            expires_in=int(data.get("expires_in") or 600),
        )
    )


@app.get("/api/downloads/<task_id>/events")
def api_events(task_id: str):
    if get_job(task_id).get("status") == "unknown":
        return jsonify({"error": "task not found"}), 404
    return jsonify({"events": task_events(task_id, _int_arg("limit", 200))})


@app.post("/api/downloads/<task_id>/queue")
def api_move_task(task_id: str):
    queue_id = str(_json_body().get("queue_id") or "").strip()
    if not queue_id:
        return jsonify({"error": "queue_id required"}), 400
    return _json_call(lambda: move_task_to_queue(task_id, queue_id))


@app.post("/api/downloads/<task_id>/priority")
def api_task_priority(task_id: str):
    data = _json_body()
    return _json_call(
        lambda: set_task_priority(task_id, int(data.get("priority") or 0))
    )


@app.post("/api/downloads/<task_id>/open")
def api_open(task_id: str):
    task = get_job(task_id)
    if task.get("status") == "unknown":
        return jsonify({"error": "not found"}), 404
    candidate = str(task.get("path") or task.get("target_dir") or "")
    if not candidate:
        return jsonify({"error": "no path available"}), 400
    path = Path(candidate)
    try:
        if sys.platform == "win32":
            command = (
                ["explorer", "/select,", str(path)]
                if path.is_file()
                else ["explorer", str(path)]
            )
        elif sys.platform == "darwin":
            command = ["open", "-R", str(path)] if path.is_file() else ["open", str(path)]
        else:
            command = ["xdg-open", str(path.parent if path.is_file() else path)]
        subprocess.Popen(command)
        return jsonify({"status": "ok", "path": str(path)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/downloads/stage")
def api_stage():
    data = _json_body()
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    return _json_call(
        lambda: stage_download(
            url,
            target_dir=_target_dir(data),
            filename=str(data.get("filename") or ""),
            download_type=str(data.get("type") or "auto"),
        )
    )


@app.post("/api/downloads/<task_id>/confirm")
def api_confirm(task_id: str):
    data = _json_body()
    return _json_call(
        lambda: confirm_staged(
            task_id,
            filename=str(data.get("filename") or "").strip(),
            target_dir=str(data.get("target_dir") or "").strip(),
            connections=int(data.get("connections") or 0),
        )
    )


@app.post("/api/downloads/clear")
def api_clear():
    return jsonify(clear_done())


@app.post("/api/downloads/pause-all")
def api_pause_all():
    return jsonify(pause_all())


@app.post("/api/downloads/resume-all")
def api_resume_all():
    return jsonify(resume_all())


@app.post("/api/downloads/cancel-all")
def api_cancel_all():
    return jsonify(cancel_all())


@app.post("/api/downloads/retry-all")
def api_retry_all():
    count = 0
    for task in list_jobs(5000):
        if task.get("status") in {"failed", "cancelled"}:
            retry_job(task["id"])
            count += 1
    return jsonify({"retried": count})


@app.post("/api/resolve")
def api_resolve():
    data = _json_body()
    envelope = data.get("request_envelope") or data
    if not str(envelope.get("url") or "").strip():
        return jsonify({"error": "url required"}), 400
    return _json_call(lambda: resolve_source(envelope))


@app.get("/api/browser/intercept-mode")
def api_intercept_mode():
    url = str(request.args.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    return jsonify({"mode": host_intercept_mode(url)})


@app.get("/api/browser/repair-pending")
def api_repair_pending():
    return jsonify({"pending": get_repair_wait()})


@app.delete("/api/browser/repair-pending")
def api_clear_repair_pending():
    clear_repair_wait()
    return jsonify({"status": "cleared"})


@app.post("/api/browser/repair-capture")
def api_repair_capture():
    data = _json_body()
    envelope = data.get("request_envelope") or data
    return _json_call(lambda: repair_from_capture(envelope))


@app.get("/api/categories")
def api_categories():
    return jsonify({"categories": list_categories()})


@app.post("/api/categories")
def api_save_category():
    data = _json_body()
    if not str(data.get("id") or "").strip():
        return jsonify({"error": "id required"}), 400
    return _json_call(lambda: save_category(data))


@app.delete("/api/categories/<category_id>")
def api_delete_category(category_id: str):
    return _json_call(
        lambda: (delete_category(category_id), {"status": "deleted"})[1]
    )


@app.get("/api/host-profiles")
def api_host_profiles():
    return jsonify({"profiles": list_host_profiles()})


@app.post("/api/host-profiles")
def api_save_host_profile():
    data = _json_body()
    profile = dict(data.get("profile") or data)
    username = data.get("username")
    password = data.get("password")
    profile.pop("username", None)
    profile.pop("password", None)
    if not str(profile.get("id") or "").strip():
        return jsonify({"error": "id required"}), 400
    return _json_call(
        lambda: save_host_profile(
            profile,
            username=None if username is None else str(username),
            password=None if password is None else str(password),
        )
    )


@app.delete("/api/host-profiles/<profile_id>")
def api_delete_host_profile(profile_id: str):
    return _json_call(
        lambda: (delete_host_profile(profile_id), {"status": "deleted"})[1]
    )


@app.get("/api/queues")
def api_queues():
    return jsonify({"queues": list_queues()})


@app.post("/api/queues")
def api_create_queue():
    data = _json_body()
    return _json_call(
        lambda: create_queue(
            str(data.get("name") or "").strip(),
            str(data.get("id") or "").strip(),
            int(data.get("max_running") or 0),
            bool(data.get("active", True)),
        )
    )


@app.patch("/api/queues/<queue_id>")
def api_update_queue(queue_id: str):
    return _json_call(lambda: update_queue(queue_id, **_json_body()))


@app.delete("/api/queues/<queue_id>")
def api_delete_queue(queue_id: str):
    return _json_call(
        lambda: (delete_queue(queue_id), {"status": "deleted"})[1]
    )


@app.get("/api/settings")
def api_settings():
    return jsonify(
        {
            "default_dir": str(DEFAULT_DIR),
            "temp_dir": str(TEMP_DIR),
            "max_concurrent": get_capabilities()["max_concurrent"],
            "completion_action": get_completion_action(),
            "default_connections": get_default_connections(),
        }
    )


@app.post("/api/settings/default-dir")
def api_default_dir():
    global DEFAULT_DIR
    value = str(_json_body().get("dir") or "").strip()
    if value:
        DEFAULT_DIR = Path(value)
        DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
        _settings["default_dir"] = str(DEFAULT_DIR)
        _write_settings(_settings)
    return jsonify({"default_dir": str(DEFAULT_DIR)})


@app.post("/api/settings/temp-dir")
def api_temp_dir():
    global TEMP_DIR
    value = str(_json_body().get("dir") or "").strip()
    if value:
        TEMP_DIR = Path(value)
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        _settings["temp_dir"] = str(TEMP_DIR)
        _write_settings(_settings)
    return jsonify({"temp_dir": str(TEMP_DIR)})


@app.post("/api/settings/concurrent")
def api_concurrent():
    data = _json_body()
    return _json_call(lambda: _save_concurrent(int(data.get("value") or 0)))


def _save_concurrent(value: int) -> dict[str, int]:
    result = set_max_concurrent(value)
    _settings["max_concurrent"] = result
    _write_settings(_settings)
    return {"max_concurrent": result}


@app.post("/api/settings/connections")
def api_connections():
    data = _json_body()
    return _json_call(lambda: _save_connections(int(data.get("value") or 0)))


def _save_connections(value: int) -> dict[str, int]:
    result = set_default_connections(value)
    _settings["default_connections"] = result
    _write_settings(_settings)
    return {"default_connections": result}


@app.route("/api/settings/completion-action", methods=["GET", "POST"])
def api_completion_action():
    if request.method == "GET":
        return jsonify({"action": get_completion_action()})
    action = set_completion_action(str(_json_body().get("action") or "none"))
    _settings["completion_action"] = action
    _write_settings(_settings)
    return jsonify({"action": action})


@app.post("/api/grab")
def api_grab():
    url = str(_json_body().get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "url must be http/https"}), 400
    try:
        links = grab_links(url)
        return jsonify({"url": url, "links": links, "count": len(links)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.post("/api/batch/crawl")
def api_batch_crawl():
    data = _json_body()
    url = str(data.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "url must be http/https"}), 400
    try:
        return jsonify(
            crawl_pages(
                url,
                max_pages=max(1, min(50, int(data.get("max_pages") or 10))),
                include_videos=bool(data.get("include_videos", True)),
                include_files=bool(data.get("include_files", True)),
            )
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.post("/api/batch/start")
def api_batch_start():
    data = _json_body()
    target = _target_dir(data)
    selected = data.get("urls")
    if selected is None:
        url = str(data.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return jsonify({"error": "url must be http/https"}), 400
        result = crawl_pages(
            url,
            max_pages=max(1, min(50, int(data.get("max_pages") or 10))),
            include_videos=bool(data.get("include_videos", True)),
            include_files=bool(data.get("include_files", True)),
        )
        links = result.get("links", [])
    else:
        types = dict(data.get("types") or {})
        links = [{"url": item, "type": types.get(item, "auto")} for item in selected]

    started: list[str] = []
    errors: list[dict[str, str]] = []
    for item in links:
        item_url = str(item.get("url") or "").strip()
        item_type = str(item.get("type") or "http")
        if not item_url:
            continue
        try:
            if item_type == "video":
                task = start_video(item_url, target_dir=target)
            elif item_type == "torrent":
                task = start_torrent(item_url, target_dir=target)
            else:
                task = start_http(
                    item_url,
                    target_dir=target,
                    temp_dir=TEMP_DIR,
                    filename=str(item.get("filename") or ""),
                )
            started.append(task["id"])
        except Exception as exc:
            errors.append({"url": item_url, "error": str(exc)})
    return jsonify(
        {
            "started": len(started),
            "errors": len(errors),
            "job_ids": started,
            "error_items": errors,
        }
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Lumi Download Manager")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7000)
    parser.add_argument("--debug", action="store_true")
    arguments = parser.parse_args(argv)

    shown_host = "localhost" if arguments.host == "0.0.0.0" else arguments.host
    print("\n  Lumi Download Manager")
    print("  -----------------------------")
    print(f"  Open: http://{shown_host}:{arguments.port}")
    print(f"  Downloads: {DEFAULT_DIR}")
    print(f"  Temporary: {TEMP_DIR}")
    print("  Press Ctrl+C to stop\n")
    app.run(
        host=arguments.host,
        port=arguments.port,
        debug=arguments.debug,
        threaded=True,
    )
