"""Reminal Download Manager — standalone Flask server.

Usage:
    python server.py              # runs on http://localhost:7000
    python server.py --port 8080  # custom port
    python server.py --host 0.0.0.0  # expose to LAN (use for mobile/PWA)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading as _net_th
import time as _net_time
from pathlib import Path

# ── Live network stats (OS-level, always-on) ──────────────────────────────────
_psutil     = None
_HAS_PSUTIL = False
try:
    import psutil as _psutil  # type: ignore[import]
    _HAS_PSUTIL = True
except ImportError:
    pass

_net_stats      = {"rx_bps": 0, "tx_bps": 0}
_net_stats_lock = _net_th.Lock()
_capacity_bps   = 0          # last speed-probe result (connection capacity)
_probe_running  = False      # prevents concurrent probes
_PROBE_INTERVAL = 120        # re-probe every 2 min when idle

# CDN URLs tried in order — first success wins. 10 MB gives an accurate result on fast links.
_PROBE_URLS = [
    "https://speed.cloudflare.com/__down?bytes=10000000",
    "https://proof.ovh.net/files/10Mb.dat",
    "https://speedtest.tele2.net/10MB.zip",
]

def _run_capacity_probe():
    """Download from a public CDN to estimate internet connection speed."""
    global _capacity_bps, _probe_running
    if _probe_running:
        return
    _probe_running = True
    try:
        import urllib.request as _ur
        _TARGET = 5 * 1024 * 1024  # stop after 5 MB — enough for an accurate read
        for url in _PROBE_URLS:
            try:
                t0    = _net_time.monotonic()
                total = 0
                with _ur.urlopen(url, timeout=15) as r:
                    while True:
                        chunk = r.read(131072)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total >= _TARGET:
                            break
                elapsed = max(0.001, _net_time.monotonic() - t0)
                bps = int(total / elapsed)
                if bps > 50_000:  # sanity: at least 50 KB/s
                    with _net_stats_lock:
                        _capacity_bps = bps
                    return
            except Exception:
                continue
    finally:
        _probe_running = False

def _net_monitor_loop():
    ps = _psutil
    if ps is None:
        return
    # Probe capacity on startup (non-blocking)
    _net_th.Thread(target=_run_capacity_probe, daemon=True).start()
    try:
        prev   = ps.net_io_counters()
        prev_t = _net_time.monotonic()
    except Exception:
        return
    last_probe_t = _net_time.monotonic()
    while True:
        _net_time.sleep(1)
        try:
            curr   = ps.net_io_counters()
            curr_t = _net_time.monotonic()
            dt     = max(0.001, curr_t - prev_t)
            with _net_stats_lock:
                _net_stats["rx_bps"] = int((curr.bytes_recv - prev.bytes_recv) / dt)
                _net_stats["tx_bps"] = int((curr.bytes_sent - prev.bytes_sent) / dt)
            prev, prev_t = curr, curr_t
            # Re-probe when idle and interval has elapsed
            if curr_t - last_probe_t > _PROBE_INTERVAL and not _probe_running:
                from core.engine import list_jobs as _list_jobs
                active = any(j.get("status") == "running" for j in _list_jobs(500))
                if not active:
                    last_probe_t = curr_t
                    _net_th.Thread(target=_run_capacity_probe, daemon=True).start()
        except Exception:
            pass

if _HAS_PSUTIL:
    _net_th.Thread(target=_net_monitor_loop, daemon=True).start()

# Always-on periodic probe — runs whether or not psutil is available
def _probe_scheduler():
    import time as _t
    _t.sleep(3)
    _net_th.Thread(target=_run_capacity_probe, daemon=True).start()
    while True:
        # Retry every 30s until we have a reading, then every _PROBE_INTERVAL
        with _net_stats_lock:
            have_reading = _capacity_bps > 0
        _t.sleep(30 if not have_reading else _PROBE_INTERVAL)
        if _probe_running:
            continue
        try:
            from core.engine import list_jobs as _lj
            if any(j.get("status") == "running" for j in _lj(500)):
                continue
        except Exception:
            pass
        _net_th.Thread(target=_run_capacity_probe, daemon=True).start()

_net_th.Thread(target=_probe_scheduler, daemon=True).start()

# When frozen by PyInstaller, bundled files are in sys._MEIPASS
_FROZEN = getattr(sys, "frozen", False)
_MEIPASS = Path(sys._MEIPASS) if _FROZEN else None

from flask import Flask, jsonify, request, send_from_directory

import json as _json

from core.engine import (
    cancel_job, clear_done, delete_job, get_capabilities, get_job,
    list_jobs, load_state, pause_job, resume_job, retry_job,
    start_http, start_torrent, start_video, verify_checksum,
    pause_all, resume_all, cancel_all, set_max_concurrent, get_video_formats,
    stage_download, confirm_staged,
    set_completion_action, get_completion_action,
    set_default_connections, get_default_connections,
)
from core.grabber import grab_links, crawl_pages

# ── Config ────────────────────────────────────────────────────────────────────

_BASE_SRC   = Path(_MEIPASS) if _FROZEN else Path(__file__).parent
STATIC_DIR  = Path(os.environ.get("LUMIDM_STATIC_DIR", str(_BASE_SRC / "static")))

_DATA_DIR   = Path(os.environ.get(
    "LUMIDM_DATA_DIR",
    str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LumiDM")
    if _FROZEN else str(Path(__file__).parent)
))
_DATA_DIR.mkdir(parents=True, exist_ok=True)

PERSIST_FILE   = _DATA_DIR / "downloads.json"
_SETTINGS_FILE = _DATA_DIR / "settings.json"

# ── Persistent settings ───────────────────────────────────────────────────────

def _load_settings() -> dict:
    try:
        return _json.loads(_SETTINGS_FILE.read_text("utf-8"))
    except Exception:
        return {}

def _save_settings(data: dict) -> None:
    try:
        tmp = _SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(_json.dumps(data), "utf-8")
        os.replace(tmp, _SETTINGS_FILE)
    except Exception:
        pass

_settings = _load_settings()

# Apply saved settings before anything else
DEFAULT_DIR = Path(_settings.get(
    "default_dir",
    os.environ.get("LUMIDM_DOWNLOAD_DIR", str(Path.home() / "Downloads"))
))

_saved_concurrent = _settings.get("max_concurrent")

app = Flask(__name__, static_folder=None)
app.config["JSON_SORT_KEYS"] = False

# Load persisted downloads from previous session
load_state(PERSIST_FILE)

# Apply saved concurrent limit after engine is loaded
if _saved_concurrent:
    try:
        set_max_concurrent(int(_saved_concurrent))
    except Exception:
        pass

# Apply saved completion action
if _settings.get("completion_action"):
    try:
        set_completion_action(_settings["completion_action"])
    except Exception:
        pass

# Apply saved default connections
if _settings.get("default_connections"):
    try:
        set_default_connections(int(_settings["default_connections"]))
    except Exception:
        pass


# ── Serve the web UI ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


# ── Capabilities ──────────────────────────────────────────────────────────────

@app.route("/api/capabilities")
def api_capabilities():
    return jsonify(get_capabilities())


# ── Download management ───────────────────────────────────────────────────────

@app.route("/api/downloads", methods=["GET"])
def api_list():
    limit = _int_arg("limit", 50)
    return jsonify({"downloads": list_jobs(limit)})


@app.route("/api/downloads/start", methods=["POST"])
def api_start():
    data = request.json or {}
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    tdir = Path(data["target_dir"]) if data.get("target_dir") else DEFAULT_DIR
    try:
        job = start_http(
            url, target_dir=tdir,
            filename=str(data.get("filename") or "").strip(),
            overwrite=bool(data.get("overwrite")),
            resume=bool(data.get("resume", True)),
            connections=int(data.get("connections") or 0),
            max_speed_bps=int(data.get("max_speed_bps") or 0),
        )
    except FileExistsError as exc:
        return jsonify({"error": str(exc)}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(job)


@app.route("/api/downloads/torrent", methods=["POST"])
def api_torrent():
    data = request.json or {}
    url = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    tdir = Path(data["target_dir"]) if data.get("target_dir") else DEFAULT_DIR
    try:
        job = start_torrent(url, target_dir=tdir,
                            connections=int(data.get("connections") or 0))
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(job)


@app.route("/api/downloads/video/formats", methods=["GET"])
def api_video_formats():
    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    try:
        return jsonify(get_video_formats(url))
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/downloads/video", methods=["POST"])
def api_video():
    data = request.json or {}
    url = str(data.get("url") or "").strip()
    if not url.startswith("http"):
        return jsonify({"error": "url must be http/https"}), 400
    tdir = Path(data["target_dir"]) if data.get("target_dir") else DEFAULT_DIR
    try:
        job = start_video(
            url, target_dir=tdir,
            format_id=str(data.get("format_id") or "bestvideo+bestaudio/best"),
            audio_only=bool(data.get("audio_only")),
            subtitles=bool(data.get("subtitles")),
        )
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(job)


@app.route("/api/downloads/<job_id>", methods=["GET"])
def api_status(job_id):
    j = get_job(job_id)
    return jsonify(j), (404 if j.get("status") == "unknown" else 200)


@app.route("/api/downloads/<job_id>/cancel", methods=["POST"])
def api_cancel(job_id):
    j = cancel_job(job_id)
    return jsonify(j), (404 if j.get("status") == "unknown" else 200)


@app.route("/api/downloads/<job_id>/pause", methods=["POST"])
def api_pause(job_id):
    j = pause_job(job_id)
    return jsonify(j), (404 if j.get("status") == "unknown" else 200)


@app.route("/api/downloads/<job_id>/resume", methods=["POST"])
def api_resume(job_id):
    j = resume_job(job_id)
    return jsonify(j), (404 if j.get("status") == "unknown" else 200)


@app.route("/api/downloads/<job_id>/retry", methods=["POST"])
def api_retry(job_id):
    j = retry_job(job_id)
    return jsonify(j), (404 if j.get("status") == "unknown" else 200)


@app.route("/api/downloads/<job_id>/delete", methods=["POST"])
def api_delete(job_id):
    body        = request.get_json(silent=True) or {}
    delete_file = bool(body.get("delete_file", False))
    r = delete_job(job_id, delete_file=delete_file)
    return jsonify(r), (404 if r.get("status") == "unknown" else 200)


@app.route("/api/downloads/<job_id>/verify", methods=["POST"])
def api_verify(job_id):
    data = request.json or {}
    expected = str(data.get("hash") or "").strip()
    algo = str(data.get("algo") or "sha256").strip()
    if not expected:
        return jsonify({"error": "hash required"}), 400
    return jsonify(verify_checksum(job_id, expected, algo))


@app.route("/api/downloads/<job_id>/open", methods=["POST"])
def api_open(job_id):
    """Open the downloaded file or its folder in the OS file manager."""
    j = get_job(job_id)
    if j.get("status") == "unknown":
        return jsonify({"error": "not found"}), 404
    path = j.get("path", "")
    if not path:
        tdir = j.get("target_dir", "")
        if not tdir:
            return jsonify({"error": "no path available"}), 400
        path = tdir
    fp = Path(path)
    try:
        if sys.platform == "win32":
            if fp.is_file():
                subprocess.Popen(["explorer", "/select,", str(fp)])
            else:
                subprocess.Popen(["explorer", str(fp.parent if fp.parent.exists() else fp)])
        elif sys.platform == "darwin":
            target = str(fp) if fp.is_file() else str(fp.parent)
            subprocess.Popen(["open", "-R", target] if fp.is_file() else ["open", target])
        else:
            subprocess.Popen(["xdg-open", str(fp.parent if fp.is_file() else fp)])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"status": "ok", "path": str(fp)})


@app.route("/api/downloads/stage", methods=["POST"])
def api_stage():
    """Probe a URL and create a staged (pending-confirmation) job."""
    data = request.json or {}
    url  = str(data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    tdir  = Path(data["target_dir"]) if data.get("target_dir") else DEFAULT_DIR
    dtype = str(data.get("type") or "auto")
    fname = str(data.get("filename") or "")
    try:
        job = stage_download(url, target_dir=tdir, filename=fname, download_type=dtype)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify(job)


@app.route("/api/downloads/<job_id>/confirm", methods=["POST"])
def api_confirm(job_id):
    """Start a staged download, optionally with user-modified filename/folder."""
    data  = request.json or {}
    fname = str(data.get("filename") or "").strip()
    tdir  = str(data.get("target_dir") or "").strip()
    conns = int(data.get("connections") or 0)
    try:
        job = confirm_staged(job_id, filename=fname, target_dir=tdir, connections=conns)
    except KeyError:
        return jsonify({"error": "job not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(job)


@app.route("/api/netstats")
def api_netstats():
    """Live OS-level network throughput + probed connection capacity."""
    with _net_stats_lock:
        data = dict(_net_stats)
        data["capacity_bps"] = _capacity_bps
    data["available"] = _HAS_PSUTIL
    return jsonify(data)


@app.route("/api/speedtest", methods=["GET"])
def api_speedtest():
    """Quick internet speed measurement — downloads ~5 MB from Cloudflare."""
    import threading as _th
    import time as _time

    TEST_URL  = "https://speed.cloudflare.com/__down?bytes=5000000"
    TIMEOUT   = 20
    result    = {}
    done_evt  = _th.Event()

    def _measure():
        global _capacity_bps
        try:
            import requests as _req
            t0   = _time.monotonic()
            r    = _req.get(TEST_URL, stream=True, timeout=TIMEOUT,
                            headers={"User-Agent": "Lumi-DM/1.0"})
            total = 0
            for chunk in r.iter_content(65536):
                total += len(chunk)
            elapsed = max(0.001, _time.monotonic() - t0)
            bps = int(total / elapsed)
            result["bps"]  = bps
            result["mbps"] = round(bps / 1_000_000, 2)
            result["bytes_downloaded"] = total
            # Update the cached capacity so widget/popup pick it up
            with _net_stats_lock:
                _capacity_bps = bps
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            done_evt.set()

    t = _th.Thread(target=_measure, daemon=True)
    t.start()
    done_evt.wait(TIMEOUT + 2)
    if not result:
        return jsonify({"error": "timeout"}), 504
    return jsonify(result)


@app.route("/api/downloads/clear", methods=["POST"])
def api_clear():
    return jsonify(clear_done())


@app.route("/api/downloads/pause-all", methods=["POST"])
def api_pause_all():
    return jsonify(pause_all())


@app.route("/api/downloads/resume-all", methods=["POST"])
def api_resume_all():
    return jsonify(resume_all())


@app.route("/api/downloads/cancel-all", methods=["POST"])
def api_cancel_all():
    return jsonify(cancel_all())


@app.route("/api/settings/completion-action", methods=["GET", "POST"])
def api_completion_action():
    if request.method == "POST":
        action = str((request.json or {}).get("action", "none"))
        result = set_completion_action(action)
        _settings["completion_action"] = result
        _save_settings(_settings)
        return jsonify({"action": result})
    return jsonify({"action": get_completion_action()})


@app.route("/api/settings/concurrent", methods=["POST"])
def api_set_concurrent():
    data = request.json or {}
    try:
        n = int(data.get("value", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "value must be an integer"}), 400
    actual = set_max_concurrent(n)
    _settings["max_concurrent"] = actual
    _save_settings(_settings)
    return jsonify({"max_concurrent": actual})


@app.route("/api/settings/default-dir", methods=["GET", "POST"])
def api_default_dir():
    global DEFAULT_DIR
    if request.method == "POST":
        d = str((request.json or {}).get("dir", "")).strip()
        if d:
            DEFAULT_DIR = Path(d)
            _settings["default_dir"] = d
            _save_settings(_settings)
        return jsonify({"default_dir": str(DEFAULT_DIR)})
    return jsonify({"default_dir": str(DEFAULT_DIR)})


@app.route("/api/settings/connections", methods=["POST"])
def api_set_connections():
    data = request.json or {}
    try:
        n = int(data.get("value", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "value must be an integer"}), 400
    actual = set_default_connections(n)
    _settings["default_connections"] = actual
    _save_settings(_settings)
    return jsonify({"default_connections": actual})


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify({
        "default_dir":        str(DEFAULT_DIR),
        "max_concurrent":     get_capabilities()["max_concurrent"],
        "completion_action":  get_completion_action(),
        "default_connections": get_default_connections(),
    })


@app.route("/api/downloads/retry-all", methods=["POST"])
def api_retry_all():
    jobs = list_jobs(200)
    retried = 0
    for j in jobs:
        if j.get("status") in {"failed", "cancelled"}:
            retry_job(j["id"])
            retried += 1
    return jsonify({"retried": retried})


@app.route("/api/grab", methods=["POST"])
def api_grab():
    data = request.json or {}
    url = str(data.get("url") or "").strip()
    if not url.startswith("http"):
        return jsonify({"error": "url must be http/https"}), 400
    links = grab_links(url)
    return jsonify({"url": url, "links": links, "count": len(links)})


@app.route("/api/batch/crawl", methods=["POST"])
def api_batch_crawl():
    """Crawl pages + follow pagination — preview results before queuing."""
    data      = request.json or {}
    url       = str(data.get("url") or "").strip()
    if not url.startswith("http"):
        return jsonify({"error": "url must be http/https"}), 400
    max_pages = max(1, min(50, int(data.get("max_pages") or 10)))
    inc_vid   = bool(data.get("include_videos", True))
    inc_files = bool(data.get("include_files",  True))
    try:
        result = crawl_pages(url, max_pages=max_pages,
                             include_videos=inc_vid, include_files=inc_files)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/batch/start", methods=["POST"])
def api_batch_start():
    """Crawl pages and immediately queue every found item for download."""
    data      = request.json or {}
    url       = str(data.get("url") or "").strip()
    if not url.startswith("http"):
        return jsonify({"error": "url must be http/https"}), 400
    max_pages  = max(1, min(50, int(data.get("max_pages") or 10)))
    inc_vid    = bool(data.get("include_videos", True))
    inc_files  = bool(data.get("include_files",  True))
    tdir       = Path(data["target_dir"]) if data.get("target_dir") else DEFAULT_DIR
    # Optional pre-selected URL list (from checkbox UI); if absent crawl fresh
    selected   = data.get("urls")   # list[str] | None

    if selected is not None:
        # User confirmed a specific subset via the UI
        links = [{"url": u, "type": data.get("types", {}).get(u, "auto")} for u in selected]
    else:
        result = crawl_pages(url, max_pages=max_pages,
                             include_videos=inc_vid, include_files=inc_files)
        links  = result.get("links", [])

    started = []
    errors  = []
    for lnk in links:
        lurl  = str(lnk.get("url") or "").strip()
        ltype = str(lnk.get("type") or "http")
        if not lurl:
            continue
        try:
            if ltype == "video":
                job = start_video(lurl, target_dir=tdir)
            elif ltype == "torrent":
                job = start_torrent(lurl, target_dir=tdir)
            else:
                job = start_http(lurl, target_dir=tdir,
                                 filename=str(lnk.get("filename") or ""),
                                 overwrite=False, resume=True)
            started.append(job["id"])
        except Exception as exc:
            errors.append({"url": lurl, "error": str(exc)})

    return jsonify({"started": len(started), "errors": len(errors),
                    "job_ids": started})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _int_arg(name: str, default: int) -> int:
    try:
        return int(request.args.get(name, default))
    except Exception:
        return default


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reminal Download Manager")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host to bind (use 0.0.0.0 for LAN/mobile access)")
    parser.add_argument("--port", type=int, default=7000, help="Port to listen on")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    args = parser.parse_args()

    print(f"\n  Reminal Download Manager")
    print(f"  -----------------------------")
    print(f"  Open: http://{args.host if args.host != '0.0.0.0' else 'localhost'}:{args.port}")
    print(f"  Default download folder: {DEFAULT_DIR}")
    print(f"  Press Ctrl+C to stop\n")

    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
