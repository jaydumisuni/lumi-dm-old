"""Runtime discovery for optional Lumi media and archive tools."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys


def _candidate_roots() -> list[Path]:
    roots = [Path(__file__).resolve().parents[2] / "tools"]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            roots.insert(0, Path(bundle))
    return roots


def find_tool(*names: str) -> str | None:
    for root in _candidate_roots():
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return str(candidate)
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def find_ffmpeg() -> str | None:
    return find_tool("ffmpeg.exe", "ffmpeg")


def find_ffprobe() -> str | None:
    return find_tool("ffprobe.exe", "ffprobe")


def find_7zip() -> str | None:
    return find_tool("7zz.exe", "7z.exe", "7zz", "7z")


def find_aria2c() -> str | None:
    return find_tool("aria2c.exe", "aria2c")


def capabilities() -> dict[str, bool | str | None]:
    return {
        "ffmpeg": find_ffmpeg(),
        "ffprobe": find_ffprobe(),
        "seven_zip": find_7zip(),
        "aria2c": find_aria2c(),
    }
