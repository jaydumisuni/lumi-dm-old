"""7-Zip archive inspection, multipart grouping and secure extraction."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
import os
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Any

from .tools import find_7zip


_ARCHIVE_EXTENSIONS = {
    ".7z", ".zip", ".rar", ".tar", ".gz", ".bz2", ".xz", ".zst",
    ".iso", ".cab", ".wim", ".vhd", ".vhdx", ".vmdk", ".dmg",
}
_MULTIPART_PATTERNS = [
    re.compile(r"^(?P<base>.+)\.part(?P<number>\d+)\.rar$", re.I),
    re.compile(r"^(?P<base>.+)\.7z\.(?P<number>\d{3,})$", re.I),
    re.compile(r"^(?P<base>.+)\.zip\.(?P<number>\d{3,})$", re.I),
    re.compile(r"^(?P<base>.+)\.z(?P<number>\d{2,})$", re.I),
    re.compile(r"^(?P<base>.+)\.r(?P<number>\d{2,})$", re.I),
]


class ArchiveUnavailable(RuntimeError):
    pass


class ArchiveSecurityError(RuntimeError):
    pass


@dataclass(slots=True)
class ArchiveLimits:
    max_files: int = 100_000
    max_unpacked_bytes: int = 250 * 1024 * 1024 * 1024
    max_ratio: float = 1_000.0


@dataclass(slots=True)
class ArchiveEntry:
    path: str
    size: int = 0
    packed_size: int = 0
    folder: bool = False
    encrypted: bool = False
    attributes: str = ""
    method: str = ""
    crc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "packed_size": self.packed_size,
            "folder": self.folder,
            "encrypted": self.encrypted,
            "attributes": self.attributes,
            "method": self.method,
            "crc": self.crc,
        }


def is_archive(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() in _ARCHIVE_EXTENSIONS or any(
        pattern.match(name) for pattern in _MULTIPART_PATTERNS
    )


def multipart_identity(path: Path) -> tuple[str, int] | None:
    name = path.name
    for pattern in _MULTIPART_PATTERNS:
        match = pattern.match(name)
        if match:
            return match.group("base").lower(), int(match.group("number"))
    return None


def group_multipart(path: Path) -> dict[str, Any]:
    identity = multipart_identity(path)
    if identity is None:
        return {
            "multipart": False,
            "first_part": str(path),
            "parts": [str(path)] if path.exists() else [],
            "missing": [],
            "complete": path.exists(),
        }
    base, _number = identity
    parts: list[tuple[int, Path]] = []
    for candidate in path.parent.iterdir():
        candidate_identity = multipart_identity(candidate)
        if candidate_identity and candidate_identity[0] == base:
            parts.append((candidate_identity[1], candidate))
    parts.sort(key=lambda item: item[0])
    if not parts:
        return {
            "multipart": True,
            "first_part": str(path),
            "parts": [],
            "missing": [1],
            "complete": False,
        }
    start = 0 if parts[0][0] == 0 else 1
    expected = list(range(start, parts[-1][0] + 1))
    present = {number for number, _candidate in parts}
    missing = [number for number in expected if number not in present]
    return {
        "multipart": True,
        "first_part": str(parts[0][1]),
        "parts": [str(candidate) for _number, candidate in parts],
        "missing": missing,
        "complete": not missing,
    }


def _safe_member_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return False
    return not any(part == ".." for part in posix.parts)


def _parse_slt(output: str) -> tuple[dict[str, Any], list[ArchiveEntry]]:
    archive: dict[str, Any] = {}
    entries: list[ArchiveEntry] = []
    current: dict[str, str] = {}
    in_entries = False

    def flush() -> None:
        nonlocal current
        if not current:
            return
        path = current.get("Path", "")
        if in_entries and path:
            entries.append(
                ArchiveEntry(
                    path=path,
                    size=int(current.get("Size") or 0),
                    packed_size=int(current.get("Packed Size") or 0),
                    folder=current.get("Folder", "-") == "+",
                    encrypted=current.get("Encrypted", "-") == "+",
                    attributes=current.get("Attributes", ""),
                    method=current.get("Method", ""),
                    crc=current.get("CRC", ""),
                )
            )
        elif path:
            archive.update(current)
        current = {}

    for raw in output.splitlines():
        line = raw.strip("\r")
        if line.startswith("----------"):
            flush()
            in_entries = True
            continue
        if not line:
            flush()
            continue
        if " = " in line:
            key, value = line.split(" = ", 1)
            current[key] = value
    flush()
    return archive, entries


class ArchiveService:
    def __init__(self, binary: str | None = None):
        self.binary = binary or find_7zip()

    def require_binary(self) -> str:
        if not self.binary:
            raise ArchiveUnavailable("Archive support requires 7-Zip/7zz")
        return self.binary

    def list(self, path: Path, *, password: str = "") -> dict[str, Any]:
        binary = self.require_binary()
        source = Path(path)
        grouping = group_multipart(source)
        if grouping["multipart"] and not grouping["complete"]:
            return {
                "status": "waiting_for_parts",
                **grouping,
                "entries": [],
            }
        source = Path(grouping["first_part"])
        command = [binary, "l", "-slt", "-ba", str(source)]
        if password:
            command.insert(2, f"-p{password}")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode not in {0, 1}:
            raise RuntimeError(self._redact(result.stderr or result.stdout, password))
        archive, entries = _parse_slt(result.stdout)
        return {
            "status": "ok",
            **grouping,
            "archive": archive,
            "entries": [entry.to_dict() for entry in entries],
            "file_count": sum(not entry.folder for entry in entries),
            "unpacked_bytes": sum(entry.size for entry in entries),
            "packed_bytes": sum(entry.packed_size for entry in entries),
            "encrypted": any(entry.encrypted for entry in entries),
        }

    def test(self, path: Path, *, password: str = "") -> dict[str, Any]:
        binary = self.require_binary()
        grouping = group_multipart(Path(path))
        if grouping["multipart"] and not grouping["complete"]:
            return {"status": "waiting_for_parts", **grouping}
        source = Path(grouping["first_part"])
        command = [binary, "t", "-bb1", str(source)]
        if password:
            command.insert(2, f"-p{password}")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60 * 60,
        )
        return {
            "status": "ok" if result.returncode == 0 else "failed",
            "returncode": result.returncode,
            "output": self._redact((result.stdout + "\n" + result.stderr)[-4000:], password),
            **grouping,
        }

    def validate(
        self,
        listing: dict[str, Any],
        *,
        archive_path: Path,
        limits: ArchiveLimits | None = None,
    ) -> None:
        limits = limits or ArchiveLimits()
        entries = [ArchiveEntry(**item) for item in listing.get("entries") or []]
        files = [entry for entry in entries if not entry.folder]
        if len(files) > limits.max_files:
            raise ArchiveSecurityError(
                f"Archive contains {len(files)} files; limit is {limits.max_files}"
            )
        total = sum(entry.size for entry in files)
        if total > limits.max_unpacked_bytes:
            raise ArchiveSecurityError(
                f"Archive expands to {total} bytes; limit is {limits.max_unpacked_bytes}"
            )
        packed = max(1, int(listing.get("packed_bytes") or archive_path.stat().st_size or 1))
        ratio = total / packed
        if ratio > limits.max_ratio:
            raise ArchiveSecurityError(
                f"Archive expansion ratio {ratio:.1f}:1 exceeds {limits.max_ratio}:1"
            )
        for entry in entries:
            if not _safe_member_path(entry.path):
                raise ArchiveSecurityError(f"Unsafe archive path: {entry.path}")
            lowered = entry.attributes.lower()
            if "symbolic" in lowered or "reparse" in lowered:
                raise ArchiveSecurityError(
                    f"Archive link/reparse entry is not allowed: {entry.path}"
                )

    def extract(
        self,
        path: Path,
        destination: Path,
        *,
        password: str = "",
        cancel_event: threading.Event | None = None,
        progress_callback: callable | None = None,
        limits: ArchiveLimits | None = None,
        delete_source: bool = False,
    ) -> dict[str, Any]:
        binary = self.require_binary()
        source = Path(path)
        listing = self.list(source, password=password)
        if listing["status"] != "ok":
            return listing
        self.validate(listing, archive_path=source, limits=limits)
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".lumi-extract-", dir=destination.parent))
        grouping = group_multipart(source)
        first_part = Path(grouping["first_part"])
        command = [
            binary,
            "x",
            "-y",
            "-bsp1",
            "-bb1",
            f"-o{staging}",
            str(first_part),
        ]
        if password:
            command.insert(2, f"-p{password}")
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output_lines: list[str] = []
        assert process.stdout is not None
        try:
            for line in process.stdout:
                output_lines.append(self._redact(line.rstrip(), password))
                percent = self._progress_percent(line)
                if percent is not None and progress_callback:
                    progress_callback(percent)
                if cancel_event and cancel_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise RuntimeError("Extraction cancelled")
            code = process.wait()
            if code != 0:
                raise RuntimeError("\n".join(output_lines[-30:]))
            self._verify_staging(staging, destination)
            moved = self._commit_staging(staging, destination)
            if delete_source:
                for item in grouping["parts"]:
                    Path(item).unlink(missing_ok=True)
            return {
                "status": "completed",
                "destination": str(destination),
                "moved": moved,
                "file_count": listing["file_count"],
                "unpacked_bytes": listing["unpacked_bytes"],
            }
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _verify_staging(staging: Path, destination: Path) -> None:
        root = staging.resolve()
        for candidate in staging.rglob("*"):
            resolved = candidate.resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ArchiveSecurityError(
                    f"Extracted path escaped staging: {candidate}"
                ) from exc
            if candidate.is_symlink():
                raise ArchiveSecurityError(
                    f"Extracted symbolic link is not allowed: {candidate}"
                )
        free = shutil.disk_usage(destination).free
        size = sum(item.stat().st_size for item in staging.rglob("*") if item.is_file())
        if size > free:
            raise OSError(f"Not enough free space to commit {size} extracted bytes")

    @staticmethod
    def _commit_staging(staging: Path, destination: Path) -> list[str]:
        moved = []
        for item in staging.iterdir():
            target = destination / item.name
            if target.exists():
                stem, suffix = target.stem, target.suffix
                index = 2
                while target.exists():
                    target = destination / f"{stem} ({index}){suffix}"
                    index += 1
            os.replace(item, target)
            moved.append(str(target))
        return moved

    @staticmethod
    def _progress_percent(line: str) -> int | None:
        match = re.search(r"(?:^|\s)(\d{1,3})%", line)
        if not match:
            return None
        return max(0, min(100, int(match.group(1))))

    @staticmethod
    def _redact(value: str, password: str) -> str:
        return value.replace(password, "<redacted>") if password else value
