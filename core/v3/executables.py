"""Runtime discovery for external engines owned by the Lumi application."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
from typing import Iterable


def _candidate_roots() -> list[Path]:
    project = Path(__file__).resolve().parents[2]
    roots = [project / "tools", project / "bin"]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
        roots.insert(1, Path(sys.executable).parent)
    return roots


def find_executable(
    names: Iterable[str],
    *,
    environment_variable: str = "",
) -> str | None:
    if environment_variable:
        configured = str(os.environ.get(environment_variable) or "").strip()
        if configured and Path(configured).is_file():
            return configured

    expanded: list[str] = []
    for name in names:
        expanded.append(name)
        if sys.platform == "win32" and not name.lower().endswith(".exe"):
            expanded.append(f"{name}.exe")

    for root in _candidate_roots():
        for name in expanded:
            candidate = root / name
            if candidate.is_file():
                return str(candidate)

    for name in expanded:
        located = shutil.which(name)
        if located:
            return located
    return None


def _bundled_imageio_ffmpeg() -> str | None:
    """Return imageio-ffmpeg's packaged binary without making it mandatory in source runs."""
    try:
        import imageio_ffmpeg  # type: ignore

        candidate = Path(imageio_ffmpeg.get_ffmpeg_exe())
        return str(candidate) if candidate.is_file() else None
    except Exception:
        return None


def find_ffmpeg() -> str | None:
    return (
        find_executable(["ffmpeg"], environment_variable="LUMIDM_FFMPEG")
        or _bundled_imageio_ffmpeg()
    )


def find_ffprobe() -> str | None:
    return find_executable(["ffprobe"], environment_variable="LUMIDM_FFPROBE")


def _windows_7zip() -> str | None:
    if sys.platform != "win32":
        return None
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = str(os.environ.get(variable) or "").strip()
        if not root:
            continue
        candidate = Path(root) / "7-Zip" / "7z.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def find_7zip() -> str | None:
    return (
        find_executable(
            ["7zz", "7z", "7za"],
            environment_variable="LUMIDM_7ZIP",
        )
        or _windows_7zip()
    )


def find_aria2c() -> str | None:
    return find_executable(["aria2c"], environment_variable="LUMIDM_ARIA2C")
