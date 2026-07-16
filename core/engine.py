"""Lumi DM — unified download engine.

Supports HTTP/HTTPS/FTP, BitTorrent/magnet, and video platform downloads.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys

def _find_ffmpeg() -> str | None:
    """Return path to ffmpeg binary, or None if not found."""
    ext  = ".exe" if __import__("sys").platform == "win32" else ""
    name = f"ffmpeg{ext}"
    here = Path(__file__).parent.parent / "tools" / name
    if here.exists():
        return str(here)
    return shutil.which("ffmpeg")
import subprocess
import threading
import time
import urllib.request
import uuid
import re as _re
from urllib.parse import unquote, urlparse, unquote_plus

import requests
from requests.adapters import HTTPAdapter

# ── Optional library detection ─────────────────────────────────────────────────

try:
    import libtorrent as _lt
    _TORRENT_LIB = "libtorrent"
except ImportError:
    _lt = None
    _TORRENT_LIB = None

try:
    import yt_dlp as _yt_dlp
    _YTDLP_AVAILABLE = True
except ImportError:
    _yt_dlp = None
    _YTDLP_AVAILABLE = False

def _find_aria2c() -> str | None:
    import sys as _sys
    ext = ".exe" if _sys.platform == "win32" else ""
    name = f"aria2c{ext}"
    candidates: list[Path] = []
    if getattr(_sys, "frozen", False):
        # PyInstaller --onefile extracts binaries to _MEIPASS
        candidates.append(Path(_sys._MEIPASS) / name)
        # Also check next to the running exe (--onedir mode)
        candidates.append(Path(_sys.executable).parent / name)
    # tools/ sibling of engine.py (dev mode or manual placement)
    candidates.append(Path(__file__).parent.parent / "tools" / name)
    for p in candidates:
        if p.exists():
            return str(p)
    return shutil.which("aria2c")

_ARIA2C_BIN = _find_aria2c()

# ── Constants ─────────────────────────────────────────────────────────────────

_CHUNK_SIZE        = 1024 * 1024
_DEFAULT_SEGMENTS  = min(32, max(4, (os.cpu_count() or 4) * 4))
_MIN_PARALLEL_SIZE = 1024 * 1024
_PROBE_TIMEOUT     = 15
_CONNECT_TIMEOUT   = 20
_READ_TIMEOUT      = 90
_MAX_CONCURRENT    = 8

_slot_semaphore = threading.Semaphore(_MAX_CONCURRENT)


def set_max_concurrent(n: int) -> int:
    """Change the max simultaneous downloads limit at runtime."""
    global _MAX_CONCURRENT, _slot_semaphore
    n = max(1, min(128, int(n)))
    _MAX_CONCURRENT = n
    _slot_semaphore = threading.Semaphore(n)
    return _MAX_CONCURRENT


def set_default_connections(n: int) -> int:
    """Change the default per-download connection count (segments) at runtime."""
    global _DEFAULT_SEGMENTS
    _DEFAULT_SEGMENTS = max(1, min(128, int(n)))
    return _DEFAULT_SEGMENTS


def get_default_connections() -> int:
    return _DEFAULT_SEGMENTS

# ── State ─────────────────────────────────────────────────────────────────────

_jobs: dict[str, dict] = {}
_lock = threading.Lock()

_UA = "Lumi-DM/1.0"

# ── Post-completion action ─────────────────────────────────────────────────────
# One of: "none" | "sleep" | "shutdown" | "restart"
_completion_action       = "none"
_completion_action_lock  = threading.Lock()
_completion_triggered    = False  # ensure we only fire once per "all done" event

def set_completion_action(action: str) -> str:
    global _completion_action, _completion_triggered
    allowed = {"none", "sleep", "shutdown", "restart"}
    action  = action if action in allowed else "none"
    with _completion_action_lock:
        _completion_action    = action
        _completion_triggered = False  # reset when user changes setting
    return action

def get_completion_action() -> str:
    with _completion_action_lock:
        return _completion_action

def _run_completion_action(action: str) -> None:
    import time as _t
    _t.sleep(5)  # brief delay so UI can show completion first
    if action == "shutdown":
        if sys.platform == "win32":
            os.system("shutdown /s /t 0")
        elif sys.platform == "darwin":
            os.system("osascript -e 'tell app \"System Events\" to shut down'")
        else:
            os.system("systemctl poweroff")
    elif action == "sleep":
        if sys.platform == "win32":
            os.system("rundll32 powrprof.dll,SetSuspendState 0,1,0")
        elif sys.platform == "darwin":
            os.system("pmset sleepnow")
        else:
            os.system("systemctl suspend")
    elif action == "restart":
        if sys.platform == "win32":
            os.system("shutdown /r /t 0")
        elif sys.platform == "darwin":
            os.system("osascript -e 'tell app \"System Events\" to restart'")
        else:
            os.system("systemctl reboot")

# ── Persistence ───────────────────────────────────────────────────────────────

_PERSIST_PATH: Path | None = None
_save_timer: threading.Timer | None = None
_save_lock = threading.Lock()


def load_state(persist_path: Path) -> None:
    global _PERSIST_PATH
    _PERSIST_PATH = Path(persist_path)
    if not _PERSIST_PATH.exists():
        return
    try:
        with _PERSIST_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        with _lock:
            for jid, j in data.items():
                if j.get("status") == "staged":
                    continue  # staged jobs expire on restart — user must re-initiate
                if j.get("status") in {"running", "probing", "queued", "pausing", "cancelling"}:
                    # Mark as paused so user can resume with one click
                    j["status"] = "paused"
                    j["error"] = ""
                    j["pause_requested"] = False
                    j["cancel_requested"] = False
                _jobs[jid] = j
    except Exception:
        pass


def _save_state() -> None:
    if not _PERSIST_PATH:
        return
    with _lock:
        data = {jid: dict(j) for jid, j in _jobs.items()}
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PERSIST_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, _PERSIST_PATH)
    except Exception:
        pass


def _schedule_save() -> None:
    global _save_timer
    with _save_lock:
        if _save_timer:
            _save_timer.cancel()
        _save_timer = threading.Timer(1.5, _save_state)
        _save_timer.daemon = True
        _save_timer.start()


# ── Capabilities ──────────────────────────────────────────────────────────────

def get_capabilities() -> dict:
    return {
        "http":           True,
        "ftp":            True,
        "torrent":        _TORRENT_LIB == "libtorrent" or bool(_ARIA2C_BIN),
        "torrent_lib":    _TORRENT_LIB or ("aria2c" if _ARIA2C_BIN else None),
        "video":          _YTDLP_AVAILABLE,
        "max_concurrent": _MAX_CONCURRENT,
        "version":        "1.0.0",
    }


# ── Public API ────────────────────────────────────────────────────────────────

def start_http(
    url: str,
    *,
    target_dir: Path,
    filename: str = "",
    overwrite: bool = False,
    resume: bool = True,
    connections: int = 0,
    max_speed_bps: int = 0,
) -> dict:
    cleaned = str(url or "").strip()
    is_ftp = cleaned.startswith("ftp://")
    if not (_is_http(cleaned) or is_ftp):
        raise ValueError("url must be http, https, or ftp")

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = (filename or "").strip() or _name_from_url(cleaned)
    final = target_dir / name
    part  = final.with_name(final.name + ".part")

    if overwrite:
        final.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
    elif final.exists():
        # Auto-rename: file (2).ext, file (3).ext …
        stem   = final.stem
        suffix = final.suffix
        n      = 2
        while final.exists():
            final = target_dir / f"{stem} ({n}){suffix}"
            n    += 1
        name = final.name
        part = final.with_name(final.name + ".part")

    conns = max(1, min(128, int(connections or _DEFAULT_SEGMENTS)))
    jid = _new_id()
    job = _blank_job(jid, "http", cleaned)
    job.update({
        "target_dir": str(target_dir), "filename": name,
        "path": str(final), "partial_path": str(part),
        "connections": conns, "max_speed_bps": int(max_speed_bps or 0),
        "overwrite": bool(overwrite), "resume": bool(resume),
    })
    _add(jid, job)
    if is_ftp:
        threading.Thread(target=_ftp_worker, args=(jid,), daemon=True).start()
    else:
        threading.Thread(target=_http_worker, args=(jid,), daemon=True).start()
    return get_job(jid)


def start_torrent(url: str, *, target_dir: Path, connections: int = 0) -> dict:
    cleaned = str(url or "").strip()
    if not (cleaned.startswith("magnet:") or cleaned.lower().endswith(".torrent")
            or _is_http(cleaned)):
        raise ValueError("url must be magnet:, a .torrent URL, or http(s)")
    if not _TORRENT_LIB and not _ARIA2C_BIN:
        raise RuntimeError(
            "Torrent support requires lbry-libtorrent (pip install lbry-libtorrent) "
            "or aria2c in PATH."
        )
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    jid = _new_id()
    job = _blank_job(jid, "torrent", cleaned)
    job.update({
        "target_dir": str(target_dir),
        "filename":   cleaned[:60] if cleaned.startswith("magnet:") else Path(cleaned).name,
        "path":       str(target_dir),
        "connections": int(connections or 0),
        "mode":       "torrent",
    })
    _add(jid, job)
    threading.Thread(target=_torrent_worker, args=(jid,), daemon=True).start()
    return get_job(jid)


def start_video(
    url: str,
    *,
    target_dir: Path,
    format_id: str = "bestvideo+bestaudio/best",
    audio_only: bool = False,
    subtitles: bool = False,
) -> dict:
    if not _YTDLP_AVAILABLE:
        raise RuntimeError("Video download requires yt-dlp: pip install yt-dlp")
    cleaned = str(url or "").strip()
    if not cleaned.startswith("http"):
        raise ValueError("url must be http or https")
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    jid = _new_id()
    job = _blank_job(jid, "video", cleaned)
    job.update({
        "target_dir": str(target_dir), "filename": "Fetching title…",
        "path": "", "format_id": format_id if not audio_only else "bestaudio/best",
        "audio_only": bool(audio_only), "subtitles": bool(subtitles), "mode": "video",
    })
    _add(jid, job)
    threading.Thread(target=_ytdlp_worker, args=(jid,), daemon=True).start()
    return get_job(jid)


def get_video_formats(url: str) -> dict:
    """Return available video qualities for a URL without downloading (uses yt-dlp)."""
    if not _YTDLP_AVAILABLE:
        raise RuntimeError("yt-dlp not installed — run: pip install yt-dlp")
    cleaned = str(url or "").strip()
    if not cleaned.startswith("http"):
        raise ValueError("url must be http/https")
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with _yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(cleaned, download=False)
    if not info:
        raise RuntimeError("Could not extract video info")

    has_ffmpeg = bool(_find_ffmpeg())
    if has_ffmpeg:
        best_fmt = "bestvideo+bestaudio/best"
        def per_fmt(h): return f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
        def per_fmt_fallback(h): return f"best[height<={h}][acodec!=none]/best[height<={h}]"
    else:
        best_fmt = "best[ext=mp4]/best"
        def per_fmt(h): return f"best[height<={h}][acodec!=none]/best[height<={h}]"
        def per_fmt_fallback(h): return per_fmt(h)

    # Group by height, prefer combined (has audio) over video-only per height
    by_height: dict = {}
    for f in (info.get("formats") or []):
        h = f.get("height")
        if not h or f.get("vcodec", "none") == "none":
            continue
        has_audio  = f.get("acodec", "none") != "none"
        prev        = by_height.get(h)
        # Replace if: no entry yet, or this one has audio and the stored one doesn't
        if prev is None or (has_audio and prev.get("acodec", "none") == "none"):
            by_height[h] = f

    fmts = []
    for h, f in sorted(by_height.items(), reverse=True):
        video_only = f.get("acodec", "none") == "none"
        needs_ff   = video_only and not has_ffmpeg
        fmts.append({
            "format_id":    per_fmt(h) if not needs_ff else per_fmt_fallback(h),
            "label":        f"{h}p",
            "height":       h,
            "ext":          "mp4",
            "needs_ffmpeg": needs_ff,
        })
    fmts.insert(0, {"format_id": best_fmt, "label": "Best quality", "height": 99999, "ext": "mp4",
                    "needs_ffmpeg": False})
    return {"url": cleaned, "title": info.get("title", ""), "formats": fmts,
            "ffmpeg": has_ffmpeg}


def get_job(job_id: str) -> dict:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else {"status": "unknown", "id": job_id}


_STAGED_MAX_AGE = 7200  # auto-cancel staged jobs older than 2 hours

def _expire_stale_staged() -> None:
    """Cancel staged jobs that were never confirmed within the time window."""
    cutoff = datetime.utcnow().isoformat()[:19]  # compare ISO timestamps lexicographically
    stale  = [
        jid for jid, j in _jobs.items()
        if j.get("status") == "staged"
        and (datetime.utcnow() - datetime.fromisoformat(j.get("created_at", datetime.utcnow().isoformat())[:19])).total_seconds() > _STAGED_MAX_AGE
    ]
    for jid in stale:
        _jobs[jid]["status"] = "cancelled"
        _jobs[jid]["error"]  = "Staged download expired"

_last_expire_check = 0.0

def list_jobs(limit: int = 50) -> list[dict]:
    global _last_expire_check
    now = time.monotonic()
    if now - _last_expire_check > 60:   # check at most once per minute
        _last_expire_check = now
        with _lock:
            _expire_stale_staged()
    with _lock:
        jobs = [dict(j) for j in _jobs.values()]
    jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jobs[: max(1, min(200, int(limit or 50)))]


def cancel_job(job_id: str) -> dict:
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            return {"status": "unknown", "id": job_id}
        if j["status"] in {"completed", "failed", "cancelled"}:
            return dict(j)
        j["cancel_requested"] = True
        j["status"] = "cancelling"
        return dict(j)


def pause_job(job_id: str) -> dict:
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            return {"status": "unknown", "id": job_id}
        if j["status"] not in {"running", "probing", "queued"}:
            return dict(j)
        j["pause_requested"] = True
        j["status"] = "pausing"
        return dict(j)


def resume_job(job_id: str) -> dict:
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            return {"status": "unknown", "id": job_id}
        if j["status"] != "paused":
            return dict(j)
        j["pause_requested"] = False
        j["cancel_requested"] = False
        j["status"] = "queued"
        jtype = j.get("type", "http")
    if jtype == "torrent":
        threading.Thread(target=_torrent_worker, args=(job_id,), daemon=True).start()
    elif jtype == "video":
        threading.Thread(target=_ytdlp_worker, args=(job_id,), daemon=True).start()
    else:
        threading.Thread(target=_http_worker, args=(job_id,), daemon=True).start()
    return get_job(job_id)


def retry_job(job_id: str) -> dict:
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            return {"status": "unknown", "id": job_id}
        if j["status"] not in {"failed", "cancelled"}:
            return dict(j)
        jtype     = j.get("type", "http")
        url       = j.get("url", "")
        tdir      = Path(j.get("target_dir", "."))
        fname     = j.get("filename", "")
        conns     = j.get("connections", _DEFAULT_SEGMENTS)
        speed     = j.get("max_speed_bps", 0)
        fmt_id    = j.get("format_id", "bestvideo+bestaudio/best")
        audio     = j.get("audio_only", False)
        subs      = j.get("subtitles", False)
        overwrite = j.get("overwrite", False)

    with _lock:
        _jobs.pop(job_id, None)

    try:
        if jtype == "torrent":
            return start_torrent(url, target_dir=tdir, connections=conns)
        elif jtype == "video":
            return start_video(url, target_dir=tdir, format_id=fmt_id,
                               audio_only=audio, subtitles=subs)
        else:
            return start_http(url, target_dir=tdir, filename=fname,
                              overwrite=True, resume=True,
                              connections=conns, max_speed_bps=speed)
    except Exception as exc:
        jid = _new_id()
        j2 = _blank_job(jid, jtype, url)
        j2.update({"status": "failed", "error": str(exc), "finished_at": _now()})
        _add(jid, j2)
        return get_job(jid)


def delete_job(job_id: str, delete_file: bool = False) -> dict:
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            return {"status": "unknown", "id": job_id}
        status = j["status"]
        if status not in {"completed", "failed", "cancelled", "paused", "cancelling", "staged"}:
            return {"status": "error", "id": job_id,
                    "error": "Cancel the download first before deleting."}
        # Force-cancel if still winding down
        if status == "cancelling":
            j["cancel_requested"] = True
        final   = Path(j.get("path", ""))
        partial = Path(j.get("partial_path", ""))

    if delete_file:
        for p in (final, partial):
            try: p.unlink(missing_ok=True)
            except Exception: pass
        # Also remove any legacy .seg.* files from old downloads
        try:
            if final.parent.exists() and final.name:
                for seg in final.parent.glob(f"{final.name}.seg.*"):
                    seg.unlink(missing_ok=True)
        except Exception: pass
    with _lock:
        _jobs.pop(job_id, None)
    _schedule_save()
    return {"status": "deleted", "id": job_id}


def stage_download(url: str, *, target_dir: Path | None = None,
                   filename: str = "", download_type: str = "auto") -> dict:
    """Probe URL and create a 'staged' job — waits for user confirmation before downloading."""
    cleaned = str(url or "").strip()
    # Return the existing staged job if the same URL is already waiting for confirmation
    with _lock:
        for j in _jobs.values():
            if j.get("status") == "staged" and j.get("url") == cleaned:
                return get_job(j["id"])
    if download_type == "auto":
        if cleaned.startswith("magnet:") or cleaned.lower().endswith(".torrent"):
            download_type = "torrent"
        elif not _is_http(cleaned):
            download_type = "http"
        else:
            download_type = "http"

    probed_size = 0
    final_url   = cleaned
    probed_name = str(filename or "").strip()

    if download_type == "http" and _is_http(cleaned):
        try:
            probe       = _head(cleaned)
            probed_size = int(probe.headers.get("Content-Length") or 0)
            final_url   = str(probe.url or cleaned)
            cd          = _parse_content_disposition(probe.headers.get("Content-Disposition", ""))
            if not probed_name and cd:
                probed_name = _sanitize_filename(cd)
        except Exception:
            pass

    if not probed_name:
        probed_name = _name_from_url(final_url)

    if target_dir is None:
        target_dir = Path(os.path.expanduser("~")) / "Downloads"

    jid = _new_id()
    job = _blank_job(jid, download_type, cleaned)
    job.update({
        "status":      "staged",
        "target_dir":  str(target_dir),
        "filename":    probed_name,
        "total_bytes": probed_size,
        "final_url":   final_url,
        "path":        str(Path(target_dir) / probed_name),
    })
    _add(jid, job)
    _schedule_save()
    return get_job(jid)


def confirm_staged(job_id: str, *, filename: str = "",
                   target_dir: str = "", connections: int = 0) -> dict:
    """Confirm and start a staged download, optionally with user-modified params."""
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            raise KeyError(f"Job {job_id} not found")
        if j.get("status") != "staged":
            raise ValueError(f"Job {job_id} is not in staged state")
        url    = j["url"]
        jtype  = j.get("type", "http")
        tdir   = Path(target_dir or j.get("target_dir", ""))
        fname  = (filename or j.get("filename", "")).strip()
        conns  = connections or 0
        _jobs.pop(job_id, None)

    if jtype == "torrent":
        return start_torrent(url, target_dir=tdir, connections=conns)
    elif jtype == "video":
        return start_video(url, target_dir=tdir)
    else:
        return start_http(url, target_dir=tdir, filename=fname,
                          connections=conns, resume=True)


def clear_done() -> dict:
    removed = 0
    with _lock:
        done = [jid for jid, j in _jobs.items()
                if j.get("status") in {"completed", "failed", "cancelled", "cancelling", "staged"}]
        for jid in done:
            if _jobs[jid].get("status") == "cancelling":
                _jobs[jid]["cancel_requested"] = True
            _jobs.pop(jid, None)
            removed += 1
    _schedule_save()
    return {"cleared": removed}


def pause_all() -> dict:
    count = 0
    with _lock:
        for j in _jobs.values():
            if j["status"] in {"running", "probing", "queued"}:
                j["pause_requested"] = True
                j["status"] = "pausing"
                count += 1
    return {"paused": count}


def resume_all() -> dict:
    ids = []
    with _lock:
        for jid, j in _jobs.items():
            if j["status"] == "paused":
                j["pause_requested"] = False
                j["cancel_requested"] = False
                j["status"] = "queued"
                ids.append((jid, j.get("type", "http")))
    for jid, jtype in ids:
        if jtype == "torrent":
            threading.Thread(target=_torrent_worker, args=(jid,), daemon=True).start()
        elif jtype == "video":
            threading.Thread(target=_ytdlp_worker, args=(jid,), daemon=True).start()
        else:
            threading.Thread(target=_http_worker, args=(jid,), daemon=True).start()
    return {"resumed": len(ids)}


def cancel_all() -> dict:
    count = 0
    with _lock:
        for j in _jobs.values():
            if j["status"] not in {"completed", "failed", "cancelled"}:
                j["cancel_requested"] = True
                j["status"] = "cancelling"
                count += 1
    return {"cancelled": count}


def verify_checksum(job_id: str, expected: str, algo: str = "sha256") -> dict:
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            return {"status": "unknown", "id": job_id}
        path   = j.get("path", "")
        status = j.get("status", "")
    if status != "completed":
        return {"status": "error", "id": job_id, "error": "Download not completed."}
    fp = Path(path)
    if not fp.exists():
        return {"status": "error", "id": job_id, "error": f"File not found: {path}"}
    algo = algo.lower().replace("-", "")
    try:
        h = hashlib.new(algo)
    except ValueError:
        return {"status": "error", "id": job_id, "error": f"Unknown algorithm: {algo}"}
    try:
        with fp.open("rb") as fh:
            while True:
                block = fh.read(1024 * 1024)
                if not block:
                    break
                h.update(block)
        actual = h.hexdigest()
        match  = actual.lower() == expected.strip().lower()
        result = "ok" if match else "mismatch"
        _update(job_id, checksum=f"{algo}:{actual}:{result}")
        return {"status": result, "id": job_id, "algo": algo,
                "expected": expected, "actual": actual}
    except Exception as exc:
        return {"status": "error", "id": job_id, "error": str(exc)}


# ── HTTP worker ───────────────────────────────────────────────────────────────

def _http_worker(jid: str) -> None:
    if not _slot_semaphore.acquire(timeout=30):
        _update(jid, status="failed",
                error="Download queue full — reduce max concurrent downloads and retry",
                finished_at=_now())
        return
    try:
        with _lock:
            j = _jobs.get(jid)
            if j is None:
                return  # job deleted before thread ran
            url, resume = j["url"], j["resume"]
            conns = j["connections"]
            speed = j.get("max_speed_bps", 0)

        _update(jid, started_at=_now())

        with _lock:
            fp   = Path(_jobs[jid]["path"])
            part = Path(_jobs[jid]["partial_path"])

        if _ARIA2C_BIN:
            # Fast path: skip Python probe — aria2c probes the URL itself internally.
            # This makes starts instant (like IDM) instead of waiting 1-2 extra round-trips.
            _update(jid, mode="aria2c", status="running")
            _aria2c_http_worker(jid, url, fp, conns, speed)
            with _lock:
                _st = _jobs.get(jid, {}).get("status")
            if _st != "failed":
                return  # completed, paused, or cancelled — done
            # aria2c failed — probe now so fallback downloader knows what it's dealing with
            fp.with_name(fp.name + ".aria2").unlink(missing_ok=True)
            _update(jid, status="probing", error=None)
            try:
                probe = _head(url)
            except Exception as exc:
                _update(jid, status="failed", error=f"Probe: {exc}", finished_at=_now())
                return
            total     = int(probe.headers.get("Content-Length") or 0)
            final_u   = str(probe.url or url)
            rangeable = "bytes" in probe.headers.get("Accept-Ranges", "").lower()
            _update(jid, total_bytes=total, final_url=final_u)
            if rangeable and total >= _MIN_PARALLEL_SIZE and conns > 1:
                _update(jid, mode="parallel", status="running", error=None)
                _parallel(jid, final_u, total, fp, part, conns, speed)
            else:
                existing = part.stat().st_size if (resume and part.exists()) else 0
                _update(jid, mode="single", segments=1, status="running",
                        error=None, downloaded_bytes=existing)
                _single(jid, final_u, total, fp, part, resume=resume,
                        existing=existing, speed=speed)
            return

        # No aria2c — probe first to know file size and whether server supports ranges
        _update(jid, status="probing")
        try:
            probe = _head(url)
        except Exception as exc:
            _update(jid, status="failed", error=f"Probe: {exc}", finished_at=_now())
            return

        total    = int(probe.headers.get("Content-Length") or 0)
        final_u  = str(probe.url or url)
        rangeable = "bytes" in probe.headers.get("Accept-Ranges", "").lower()

        cd_name = _parse_content_disposition(probe.headers.get("Content-Disposition", ""))
        cd_name = _sanitize_filename(cd_name) if cd_name else ""
        if cd_name:
            with _lock:
                j = _jobs.get(jid, {})
                cur_name = j.get("filename", "")
                if not cur_name or _B64_RE.match(cur_name.split(".")[0]) or cur_name == "download.bin":
                    tdir_path = Path(j.get("target_dir", "."))
                    new_fp    = tdir_path / cd_name
                    new_part  = new_fp.with_name(cd_name + ".part")
                    _jobs[jid].update({
                        "filename":     cd_name,
                        "path":         str(new_fp),
                        "partial_path": str(new_part),
                    })

        _update(jid, total_bytes=total, final_url=final_u)

        with _lock:
            fp   = Path(_jobs[jid]["path"])
            part = Path(_jobs[jid]["partial_path"])

        if total > 0:
            try:
                free = shutil.disk_usage(fp.parent).free
                if free < total:
                    mb_need = total // 1048576
                    mb_free = free  // 1048576
                    _update(jid, status="failed", finished_at=_now(),
                            error=f"Not enough disk space: need {mb_need} MB, have {mb_free} MB free")
                    return
            except OSError:
                pass

        if rangeable and total >= _MIN_PARALLEL_SIZE and conns > 1:
            _update(jid, mode="parallel", status="running")
            _parallel(jid, final_u, total, fp, part, conns, speed)
        else:
            existing = part.stat().st_size if (resume and part.exists()) else 0
            _update(jid, mode="single", segments=1, status="running",
                    downloaded_bytes=existing)
            _single(jid, final_u, total, fp, part, resume=resume,
                    existing=existing, speed=speed)
    except Exception as exc:
        _update(jid, status="failed", error=f"Worker error: {exc}", finished_at=_now())
    finally:
        _slot_semaphore.release()


def _aria2c_http_worker(jid, url, fp, conns, speed):
    """Native aria2c HTTP download — bypasses Python GIL for true parallel I/O speed."""
    tdir     = str(fp.parent)
    filename = fp.name
    splits   = min(conns, 32)
    cmd = [
        _ARIA2C_BIN,
        "--dir",                      tdir,
        "--out",                      filename,
        f"--max-connection-per-server={splits}",
        f"--split={splits}",
        "--min-split-size=1M",
        "--continue=true",
        "--file-allocation=none",
        "--disk-cache=64M",
        "--console-log-level=notice",
        "--summary-interval=1",
        "--human-readable=false",
        f"--user-agent={_UA}",
        "--max-tries=5",
        "--retry-wait=3",
        "--connect-timeout=20",
        "--timeout=60",
        "--async-dns=false",
    ]
    if speed > 0:
        cmd += [f"--max-download-limit={speed}"]
    cmd.append(url)

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        t0 = time.monotonic()
        while True:
            if _is_cancelled(jid):
                proc.terminate()
                _update(jid, status="cancelled", finished_at=_now())
                def _del(f1, f2):
                    try: proc.wait(timeout=3)
                    except Exception: proc.kill()
                    f1.unlink(missing_ok=True)
                    f2.unlink(missing_ok=True)
                threading.Thread(target=_del,
                                 args=(fp, fp.with_name(fp.name + ".aria2")),
                                 daemon=True).start()
                return
            if _is_paused(jid):
                proc.terminate(); proc.wait()
                _update(jid, status="paused")
                return
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            # [#hash downloaded/total(pct%) CN:N DL:speed ETA:Xs]
            m = _re.search(r"(\d+)/(\d+)\((\d+)%\).*?DL:(\d+)", line)
            if m:
                dl    = int(m.group(1))
                total = int(m.group(2))
                spd   = int(m.group(4))
                _update(jid, downloaded_bytes=dl, total_bytes=total,
                        progress_percent=float(m.group(3)),
                        speed_bytes_per_sec=float(spd))
        rc = proc.wait()
        if rc == 0:
            sz = fp.stat().st_size if fp.exists() else 0
            el = max(0.001, time.monotonic() - t0)
            _update(jid, status="completed", finished_at=_now(),
                    progress_percent=100.0, downloaded_bytes=sz, total_bytes=sz,
                    speed_bytes_per_sec=round(sz / el, 2), path=str(fp))
        else:
            _update(jid, status="failed", error=f"aria2c exit {rc}", finished_at=_now())
    except Exception as exc:
        _update(jid, status="failed", error=str(exc), finished_at=_now())


def _parallel(jid, url, total, fp, part, conns, speed):
    """IDM-style parallel download: one .part file pre-allocated, segments seek to offset."""
    segs      = min(conns, max(1, total // _MIN_PARALLEL_SIZE))
    seg_size  = total // segs
    ranges    = [(i * seg_size, (i * seg_size + seg_size - 1) if i < segs - 1 else total - 1)
                 for i in range(segs)]
    _update(jid, segments=segs)

    # Pre-allocate the .part file at full size so user sees one file, not 32
    if not part.exists() or part.stat().st_size != total:
        try:
            with part.open("wb") as fh:
                if total > 0:
                    fh.seek(total - 1)
                    fh.write(b"\x00")
        except OSError as exc:
            _update(jid, status="failed", error=f"Allocate: {exc}", finished_at=_now())
            return

    # One shared session — all segments reuse the same TCP/TLS connections
    sess = requests.Session()
    sess.trust_env = False
    _adp = HTTPAdapter(pool_connections=1, pool_maxsize=segs + 4)
    sess.mount("http://", _adp)
    sess.mount("https://", _adp)

    # Per-segment byte counters (thread-safe via GIL on int writes)
    seg_bytes = [0] * segs
    seg_done  = [False] * segs
    errors    = []
    errlk     = threading.Lock()
    t0        = time.monotonic()
    seg_limit = int(speed / segs) if speed and segs else 0

    # Shared abort signal — set by reporter, closes all active connections instantly
    _abort     = threading.Event()
    _responses = []
    _resp_lock = threading.Lock()

    # 5-second sliding window for accurate live speed
    _sw_lock   = threading.Lock()
    _sw_points = []  # list of (monotonic_time, total_bytes)

    def _reporter():
        while not all(seg_done):
            if _is_cancelled(jid) or _is_paused(jid):
                _abort.set()
                with _resp_lock:
                    for r in list(_responses):
                        try: r.close()
                        except Exception: pass
                return
            dl  = sum(seg_bytes)
            now = time.monotonic()
            with _sw_lock:
                _sw_points.append((now, dl))
                cutoff = now - 5.0
                while _sw_points and _sw_points[0][0] < cutoff:
                    _sw_points.pop(0)
                if len(_sw_points) >= 2:
                    dt  = _sw_points[-1][0] - _sw_points[0][0]
                    db  = _sw_points[-1][1] - _sw_points[0][1]
                    spd = db / max(0.001, dt)
                else:
                    spd = dl / max(0.001, now - t0)
            _update(jid, downloaded_bytes=dl, progress_percent=_pct(dl, total),
                    speed_bytes_per_sec=round(spd, 2))
            time.sleep(0.4)

    threading.Thread(target=_reporter, daemon=True).start()

    def _seg(idx, sess):
        s, e = ranges[idx]
        written = seg_bytes[idx]  # resume offset within segment
        cur     = s + written      # current byte position in file
        if cur > e:
            seg_done[idx] = True
            return
        current_url = url  # may be refreshed on 401/403/410
        hdrs = {"User-Agent": _UA, "Range": f"bytes={cur}-{e}"}
        tb, ts = 0, time.monotonic()
        for attempt in range(3):
            try:
                try:
                    r = sess.get(current_url, stream=True, allow_redirects=True,
                                 timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT), headers=hdrs)
                except requests.exceptions.SSLError:
                    r = sess.get(current_url, stream=True, allow_redirects=True,
                                 timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT), headers=hdrs,
                                 verify=False)
                if r.status_code in (401, 403, 410) and attempt < 2:
                    try:
                        with _lock:
                            orig = _jobs.get(jid, {}).get("url", current_url)
                        fresh = _head(orig)
                        new_url = str(fresh.url or orig)
                        if new_url != current_url:
                            current_url = new_url
                    except Exception:
                        pass
                r.raise_for_status()
                with _resp_lock:
                    _responses.append(r)
                try:
                    with part.open("r+b") as fh:
                        fh.seek(cur)
                        for chunk in r.iter_content(_CHUNK_SIZE):
                            if not chunk: continue
                            if _abort.is_set():
                                seg_done[idx] = True
                                return
                            fh.write(chunk)
                            seg_bytes[idx] += len(chunk)
                            cur            += len(chunk)
                            hdrs["Range"]   = f"bytes={cur}-{e}"
                            if seg_limit:
                                tb += len(chunk)
                                exp = tb / seg_limit
                                act = time.monotonic() - ts
                                if exp > act: time.sleep(exp - act)
                finally:
                    with _resp_lock:
                        try: _responses.remove(r)
                        except ValueError: pass
                break  # success — exit retry loop
            except Exception as exc:
                if attempt == 2:
                    with errlk: errors.append(f"seg{idx}: {exc}")
                else:
                    time.sleep(2 ** attempt)  # 1s then 2s backoff
        seg_done[idx] = True

    threads = [threading.Thread(target=_seg, args=(i, sess), daemon=True) for i in range(segs)]
    for t in threads: t.start()
    for t in threads: t.join()
    sess.close()

    if _is_cancelled(jid):
        _update(jid, status="cancelled", finished_at=_now()); return
    if _is_paused(jid):
        _update(jid, status="paused"); return
    if errors:
        _update(jid, status="failed", error=" | ".join(errors), finished_at=_now()); return

    # Rename .part → final (instant — no merge step)
    try:
        os.replace(part, fp)
    except Exception as exc:
        _update(jid, status="failed", error=f"Rename: {exc}", finished_at=_now()); return

    el = max(0.001, time.monotonic() - t0)
    sz = fp.stat().st_size if fp.exists() else total
    _update(jid, status="completed", finished_at=_now(),
            downloaded_bytes=sz, progress_percent=100.0,
            speed_bytes_per_sec=round(sz / el, 2))


def _single(jid, url, total, fp, part, *, resume, existing, speed):
    t0          = time.monotonic()
    dl          = existing
    sw_pts      = []  # sliding-window speed: list of (time, bytes)
    current_url = url  # may be refreshed on 401/403/410

    for attempt in range(3):
        hdrs = {"User-Agent": _UA}
        if dl and resume:
            hdrs["Range"] = f"bytes={dl}-"
        tb, ts = 0, time.monotonic()
        try:
            r  = _get(current_url, hdrs)
            rt = _resolve_total(r, dl, hdrs)
            _update(jid, total_bytes=rt, final_url=str(r.url or current_url))
            mode = "ab" if dl and r.status_code == 206 else "wb"
            if mode == "wb": dl = 0
            with part.open(mode) as fh:
                for chunk in r.iter_content(_CHUNK_SIZE):
                    if not chunk: continue
                    if _is_cancelled(jid):
                        _update(jid, status="cancelled", finished_at=_now()); return
                    if _is_paused(jid):
                        _update(jid, status="paused"); return
                    fh.write(chunk)
                    dl += len(chunk)
                    now = time.monotonic()
                    sw_pts.append((now, dl))
                    cutoff = now - 5.0
                    while sw_pts and sw_pts[0][0] < cutoff:
                        sw_pts.pop(0)
                    if len(sw_pts) >= 2:
                        dt  = sw_pts[-1][0] - sw_pts[0][0]
                        db  = sw_pts[-1][1] - sw_pts[0][1]
                        spd = db / max(0.001, dt)
                    else:
                        spd = dl / max(0.001, now - t0)
                    _update(jid, downloaded_bytes=dl, total_bytes=rt,
                            progress_percent=_pct(dl, rt),
                            speed_bytes_per_sec=round(spd, 2))
                    if speed:
                        tb += len(chunk)
                        exp = tb / speed
                        act = time.monotonic() - ts
                        if exp > act: time.sleep(exp - act)
                        if time.monotonic() - ts > 2.0:
                            ts, tb = time.monotonic(), 0
            # Success
            os.replace(part, fp)
            sz = fp.stat().st_size if fp.exists() else dl
            el = max(0.001, time.monotonic() - t0)
            _update(jid, status="completed", finished_at=_now(),
                    downloaded_bytes=sz, progress_percent=100.0,
                    speed_bytes_per_sec=round(sz / el, 2))
            return
        except requests.exceptions.HTTPError as he:
            sc = he.response.status_code if he.response is not None else 0
            if sc in (401, 403, 410) and attempt < 2:
                try:
                    with _lock:
                        orig = _jobs.get(jid, {}).get("url", current_url)
                    fresh = _head(orig)
                    new_url = str(fresh.url or orig)
                    if new_url != current_url:
                        current_url = new_url
                except Exception:
                    pass
            if attempt == 2:
                _update(jid, status="failed", error=str(he), finished_at=_now())
            else:
                dl = part.stat().st_size if part.exists() else dl
                time.sleep(2 ** attempt)
        except Exception as exc:
            if attempt == 2:
                _update(jid, status="failed", error=str(exc), finished_at=_now())
            else:
                dl = part.stat().st_size if part.exists() else dl
                time.sleep(2 ** attempt)


# ── FTP worker ────────────────────────────────────────────────────────────────

def _ftp_worker(jid: str) -> None:
    _slot_semaphore.acquire()
    try:
        with _lock:
            j = dict(_jobs[jid])
        url   = j["url"]
        fp    = Path(j["path"])
        part  = Path(j["partial_path"])
        resume = j.get("resume", True)

        _update(jid, status="running", started_at=_now(), mode="single")
        t0 = time.monotonic()
        try:
            existing = part.stat().st_size if (resume and part.exists()) else 0
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            if existing:
                req.add_header("Range", f"bytes={existing}-")
            with urllib.request.urlopen(req, timeout=_CONNECT_TIMEOUT) as r:
                total = int(r.headers.get("Content-Length") or 0)
                if existing and total:
                    total += existing
                _update(jid, total_bytes=total)
                dl = existing
                mode = "ab" if existing else "wb"
                with part.open(mode) as fh:
                    while True:
                        if _is_cancelled(jid):
                            _update(jid, status="cancelled", finished_at=_now())
                            return
                        if _is_paused(jid):
                            _update(jid, status="paused")
                            return
                        chunk = r.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        fh.write(chunk)
                        dl += len(chunk)
                        el = max(0.001, time.monotonic() - t0)
                        _update(jid, downloaded_bytes=dl,
                                total_bytes=max(total, dl),
                                progress_percent=_pct(dl, total) if total else 0.0,
                                speed_bytes_per_sec=round(dl / el, 2))
            os.replace(part, fp)
            sz = fp.stat().st_size if fp.exists() else dl
            el = max(0.001, time.monotonic() - t0)
            _update(jid, status="completed", finished_at=_now(),
                    downloaded_bytes=sz, progress_percent=100.0,
                    speed_bytes_per_sec=round(sz / el, 2))
        except Exception as exc:
            _update(jid, status="failed", error=str(exc), finished_at=_now())
    finally:
        _slot_semaphore.release()


# ── Torrent worker ────────────────────────────────────────────────────────────

def _torrent_worker(jid: str) -> None:
    _slot_semaphore.acquire()
    try:
        with _lock:
            j = dict(_jobs[jid])
        _update(jid, status="running", started_at=_now())
        if _TORRENT_LIB == "libtorrent":
            _lt_worker(jid, j)
        elif _ARIA2C_BIN:
            _aria2c_worker(jid, j)
        else:
            _update(jid, status="failed",
                    error="Install lbry-libtorrent or aria2c for torrent support.",
                    finished_at=_now())
    finally:
        _slot_semaphore.release()


def _lt_worker(jid, j):
    ses = _lt.session()
    ses.listen_on(6881, 6891)
    ses.add_dht_router("router.bittorrent.com", 6881)
    ses.add_dht_router("router.utorrent.com", 6881)
    ses.start_dht()
    url = j["url"]
    tdir = j["target_dir"]
    try:
        if url.startswith("magnet:"):
            params = _lt.parse_magnet_uri(url)
            params.save_path = tdir
            handle = ses.add_torrent(params)
        else:
            if _is_http(url):
                r = requests.get(url, timeout=30, headers={"User-Agent": _UA})
                r.raise_for_status()
                info = _lt.torrent_info(_lt.bdecode(r.content))
            else:
                info = _lt.torrent_info(url)
            handle = ses.add_torrent({"ti": info, "save_path": tdir})
        while True:
            if _is_cancelled(jid):
                ses.remove_torrent(handle)
                _update(jid, status="cancelled", finished_at=_now()); return
            if _is_paused(jid):
                handle.pause()
                _update(jid, status="paused"); return
            s = handle.status()
            name = str(handle.name() or j["filename"])
            dl = int(s.total_done)
            total = int(s.total_wanted) if s.total_wanted > 0 else 0
            _update(jid, filename=name, downloaded_bytes=dl, total_bytes=total,
                    progress_percent=round(s.progress * 100, 2),
                    speed_bytes_per_sec=int(s.download_rate),
                    path=str(Path(tdir) / name))
            if s.is_seeding:
                fname = str(handle.name() or j["filename"])
                _update(jid, status="completed", finished_at=_now(),
                        filename=fname, path=str(Path(tdir) / fname),
                        progress_percent=100.0); return
            time.sleep(1.0)
    except Exception as exc:
        _update(jid, status="failed", error=str(exc), finished_at=_now())


def _aria2c_worker(jid, j):
    import re as _re
    url = j["url"]
    tdir = j["target_dir"]
    cmd = [_ARIA2C_BIN, "--dir", tdir, "--max-connection-per-server=16",
           "--split=16", "--seed-time=0", "--console-log-level=notice",
           "--summary-interval=2", url]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        t0 = time.monotonic()
        while True:
            if _is_cancelled(jid):
                proc.terminate()
                _update(jid, status="cancelled", finished_at=_now()); return
            if _is_paused(jid):
                proc.terminate()
                _update(jid, status="paused"); return
            line = proc.stdout.readline()
            if not line and proc.poll() is not None: break
            m = _re.search(
                r"(\d+(?:\.\d+)?)(Ki?B|Mi?B|Gi?B)/(\d+(?:\.\d+)?)(Ki?B|Mi?B|Gi?B)\((\d+)%\)",
                line)
            if m:
                def _b(v, u):
                    v = float(v); u = u.upper().replace("I","")
                    return int(v * {"KB":1024,"MB":1048576,"GB":1073741824}.get(u, 1))
                dl = _b(m.group(1), m.group(2))
                total = _b(m.group(3), m.group(4))
                el = max(0.001, time.monotonic() - t0)
                _update(jid, downloaded_bytes=dl, total_bytes=total,
                        progress_percent=float(m.group(5)),
                        speed_bytes_per_sec=round(dl / el, 2))
        rc = proc.wait()
        if rc == 0:
            _update(jid, status="completed", finished_at=_now(), progress_percent=100.0)
        else:
            _update(jid, status="failed", error=f"aria2c exit {rc}", finished_at=_now())
    except Exception as exc:
        _update(jid, status="failed", error=str(exc), finished_at=_now())


# ── yt-dlp video worker ───────────────────────────────────────────────────────

def _ytdlp_worker(jid: str) -> None:
    _slot_semaphore.acquire()
    try:
        with _lock:
            j = dict(_jobs[jid])
        _update(jid, status="running", started_at=_now())
        url = j["url"]
        tdir = j["target_dir"]
        fmt = j.get("format_id", "bestvideo+bestaudio/best")
        audio = j.get("audio_only", False)
        subs  = j.get("subtitles", False)
        cancelled = [False]

        def _hook(d):
            if cancelled[0]: raise Exception("Cancelled")
            if d.get("status") == "downloading":
                dl = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                spd   = d.get("speed") or 0
                fname = d.get("filename", "")
                _update(jid, downloaded_bytes=int(dl), total_bytes=int(total),
                        progress_percent=_pct(int(dl), int(total)),
                        speed_bytes_per_sec=float(spd),
                        filename=Path(fname).name if fname else j["filename"])
                if _is_cancelled(jid) or _is_paused(jid):
                    cancelled[0] = True
            elif d.get("status") == "finished":
                fname = d.get("filename", "")
                if fname:
                    _update(jid, filename=Path(fname).name, path=fname)

        ffmpeg = _find_ffmpeg()
        # If ffmpeg is unavailable, fall back to a pre-merged single-stream format
        if not ffmpeg and ("+" in fmt or fmt.startswith("bestvideo")):
            fmt = "best[ext=mp4]/best"
        if not ffmpeg and audio:
            audio = False  # can't extract audio without ffmpeg

        pp = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}] if (audio and ffmpeg) else []
        opts = {
            "outtmpl":          str(Path(tdir) / "%(title)s.%(ext)s"),
            "format":           fmt,
            "progress_hooks":   [_hook],
            "writesubtitles":   subs,
            "writeautomaticsub": subs,
            "postprocessors":   pp,
            "quiet":            True,
            "no_warnings":      True,
            "noplaylist":       True,
        }
        if ffmpeg:
            opts["ffmpeg_location"] = ffmpeg
        try:
            with _yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    fname = ydl.prepare_filename(info)
                    _update(jid, status="completed", finished_at=_now(),
                            progress_percent=100.0, filename=Path(fname).name,
                            path=fname,
                            downloaded_bytes=int(info.get("filesize") or
                                                 info.get("filesize_approx") or 0),
                            total_bytes=int(info.get("filesize") or
                                            info.get("filesize_approx") or 0))
                else:
                    _update(jid, status="completed", finished_at=_now(),
                            progress_percent=100.0)
        except Exception as exc:
            msg = str(exc)
            if "Cancelled" in msg or cancelled[0]:
                if _is_paused(jid) or j.get("pause_requested"):
                    _update(jid, status="paused")
                else:
                    _update(jid, status="cancelled", finished_at=_now())
            else:
                _update(jid, status="failed", error=msg, finished_at=_now())
    finally:
        _slot_semaphore.release()


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _head(url):
    with requests.Session() as s:
        s.trust_env = False
        def _get_fallback(verify=True):
            # Some CDNs (Apple, etc.) reject HEAD — a Range-GET reveals the same headers
            r = s.get(url, stream=True, allow_redirects=True,
                      timeout=_PROBE_TIMEOUT, verify=verify,
                      headers={"User-Agent": _UA, "Range": "bytes=0-0"})
            r.close()
            return r
        try:
            r = s.head(url, allow_redirects=True, timeout=_PROBE_TIMEOUT,
                       headers={"User-Agent": _UA})
            if r.status_code >= 400:
                # HEAD rejected (405) or forbidden (403) — try minimal GET
                r = _get_fallback()
                if r.status_code not in (200, 206):
                    r.raise_for_status()
            return r
        except requests.exceptions.SSLError:
            r = _get_fallback(verify=False)
            if r.status_code not in (200, 206):
                r.raise_for_status()
            return r


def _get(url, headers):
    s = requests.Session()
    s.trust_env = False
    try:
        r = s.get(url, stream=True, allow_redirects=True,
                  timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT), headers=headers)
        r.raise_for_status()
        return r
    except requests.exceptions.SSLError:
        r = s.get(url, stream=True, allow_redirects=True,
                  timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT), headers=headers,
                  verify=False)
        r.raise_for_status()
        return r


# ── Utilities ─────────────────────────────────────────────────────────────────

def _resolve_total(r, existing, hdrs):
    raw = int(r.headers.get("Content-Length") or 0)
    if hdrs.get("Range") and r.status_code == 206 and raw:
        return existing + raw
    return raw

def _pct(dl, total): return round((dl / total) * 100.0, 2) if total > 0 else 0.0
def _is_http(u): p = urlparse(u); return p.scheme in {"http","https"} and bool(p.netloc)

def _parse_content_disposition(header: str) -> str:
    """Extract filename from Content-Disposition header (supports RFC 5987 filename*)."""
    if not header:
        return ""
    # filename*=UTF-8''encoded_name  (RFC 5987)
    m = _re.search(r"filename\*\s*=\s*(?:[Uu][Tt][Ff]-8''|[Uu][Tt][Ff]8'')([^;\r\n]+)", header, _re.I)
    if m:
        try:
            return Path(unquote(m.group(1).strip())).name
        except Exception:
            pass
    # filename="name" or filename=name
    m = _re.search(r'filename\s*=\s*"([^"]+)"', header, _re.I)
    if m:
        return Path(m.group(1).strip()).name
    m = _re.search(r"filename\s*=\s*'([^']+)'", header, _re.I)
    if m:
        return Path(m.group(1).strip()).name
    m = _re.search(r'filename\s*=\s*([^\s;"\r\n]+)', header, _re.I)
    if m:
        return Path(unquote_plus(m.group(1).strip())).name
    return ""

_SAFE_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789.-_() []"
)
_B64_RE = _re.compile(r'^[A-Za-z0-9+/=]{20,}$')

def _sanitize_filename(name: str) -> str:
    """Remove illegal chars; if name looks like base64 garbage, return empty."""
    if not name:
        return ""
    # Strip known-bad Windows chars
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "_")
    name = name.strip(". ")
    # Detect base64-like garbage (no extension, long run of base64 chars)
    stem, _, ext = name.rpartition(".")
    if not ext or len(ext) > 8:
        if _B64_RE.match(name):
            return ""
    return name[:200] or ""

def _name_from_url(url: str) -> str:
    """Best-effort filename from URL path, with sanitization."""
    try:
        raw = Path(unquote(urlparse(url).path or "")).name
    except Exception:
        raw = ""
    name = _sanitize_filename(raw)
    return name or "download.bin"
def _new_id(): return uuid.uuid4().hex[:10]
def _now(): return datetime.utcnow().isoformat() + "Z"
def _is_cancelled(jid):
    with _lock: return bool(_jobs.get(jid, {}).get("cancel_requested"))
def _is_paused(jid):
    with _lock: return bool(_jobs.get(jid, {}).get("pause_requested"))
def _update(jid, **kw):
    global _completion_triggered
    with _lock:
        if jid in _jobs:
            _jobs[jid].update(kw)
    if kw.get("status") in {"completed", "failed", "cancelled", "paused"}:
        _schedule_save()
    if kw.get("status") == "completed":
        _check_completion_action()

def _check_completion_action():
    global _completion_triggered
    with _completion_action_lock:
        action    = _completion_action
        triggered = _completion_triggered
    if action == "none" or triggered:
        return
    with _lock:
        still_running = any(
            j.get("status") in {"running", "queued"}
            for j in _jobs.values()
        )
    if still_running:
        return
    with _completion_action_lock:
        if _completion_triggered:
            return
        _completion_triggered = True
    threading.Thread(target=_run_completion_action, args=(action,), daemon=True).start()
def _add(jid, job):
    with _lock: _jobs[jid] = job

def _blank_job(jid, jtype, url):
    return {
        "id": jid, "type": jtype, "url": url,
        "status": "queued", "target_dir": "", "filename": "",
        "path": "", "partial_path": "", "final_url": url,
        "downloaded_bytes": 0, "total_bytes": 0,
        "progress_percent": 0.0, "speed_bytes_per_sec": 0.0,
        "connections": _DEFAULT_SEGMENTS, "segments": 1, "mode": "single",
        "max_speed_bps": 0, "overwrite": False, "resume": True,
        "cancel_requested": False, "pause_requested": False,
        "error": "", "checksum": "",
        "created_at": _now(), "started_at": "", "finished_at": "",
    }
