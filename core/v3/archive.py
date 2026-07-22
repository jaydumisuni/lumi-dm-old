"""7-Zip archive inspection, multipart grouping and secure staged extraction."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import threading
from typing import Callable, Iterable
import uuid

from .executables import find_7zip
from .models import ArchiveEntry


class ArchiveError(RuntimeError):
    pass


class ArchivePasswordRequired(ArchiveError):
    pass


class ArchiveCancelled(ArchiveError):
    pass


@dataclass(slots=True)
class ArchiveParts:
    primary: Path
    parts: list[Path]
    missing: list[str]
    kind: str

    @property
    def ready(self) -> bool:
        return not self.missing and self.primary.is_file()

    def to_dict(self) -> dict:
        return {
            "primary": str(self.primary),
            "parts": [str(item) for item in self.parts],
            "missing": list(self.missing),
            "kind": self.kind,
            "ready": self.ready,
        }


_PART_RAR = re.compile(r"^(?P<base>.+)\.part(?P<number>\d+)\.rar$", re.I)
_NUMBERED = re.compile(r"^(?P<base>.+\.(?:7z|zip))\.(?P<number>\d{3})$", re.I)
_ZIP_VOLUME = re.compile(r"^(?P<base>.+)\.z(?P<number>\d{2})$", re.I)
_RAR_VOLUME = re.compile(r"^(?P<base>.+)\.r(?P<number>\d{2})$", re.I)
_GENERIC_VOLUME = re.compile(r"^(?P<base>.+)\.(?P<number>\d{3})$", re.I)
_PERCENT = re.compile(r"(?<!\d)(\d{1,3})%")
_DRIVE = re.compile(r"^[A-Za-z]:")


def _ordered_with_gaps(
    found: dict[int, Path],
    *,
    first: int,
    label: Callable[[int], str],
) -> tuple[list[Path], list[str]]:
    if not found:
        return [], [label(first)]
    maximum = max(found)
    missing = [label(index) for index in range(first, maximum + 1) if index not in found]
    return [found[index] for index in sorted(found)], missing


def discover_archive_parts(path: Path) -> ArchiveParts:
    path = Path(path)
    directory = path.parent
    name = path.name

    match = _PART_RAR.match(name)
    if match:
        base = match.group("base")
        found: dict[int, Path] = {}
        for candidate in directory.iterdir():
            current = _PART_RAR.match(candidate.name)
            if current and current.group("base").lower() == base.lower():
                found[int(current.group("number"))] = candidate
        parts, missing = _ordered_with_gaps(
            found,
            first=1,
            label=lambda number: f"{base}.part{number}.rar",
        )
        primary = found.get(1, directory / f"{base}.part1.rar")
        return ArchiveParts(primary, parts, missing, "rar-parts")

    match = _NUMBERED.match(name)
    if match:
        base = match.group("base")
        found = {}
        pattern = re.compile(re.escape(base) + r"\.(\d{3})$", re.I)
        for candidate in directory.iterdir():
            current = pattern.match(candidate.name)
            if current:
                found[int(current.group(1))] = candidate
        parts, missing = _ordered_with_gaps(
            found,
            first=1,
            label=lambda number: f"{base}.{number:03d}",
        )
        primary = found.get(1, directory / f"{base}.001")
        return ArchiveParts(primary, parts, missing, "numbered-archive")

    match = _ZIP_VOLUME.match(name)
    if match or path.suffix.lower() == ".zip":
        base = match.group("base") if match else path.with_suffix("").name
        primary = directory / f"{base}.zip"
        found = {}
        pattern = re.compile(re.escape(base) + r"\.z(\d{2})$", re.I)
        for candidate in directory.iterdir():
            current = pattern.match(candidate.name)
            if current:
                found[int(current.group(1))] = candidate
        parts, missing = _ordered_with_gaps(
            found,
            first=1,
            label=lambda number: f"{base}.z{number:02d}",
        ) if found else ([], [])
        if not primary.is_file():
            missing.append(primary.name)
        if primary.is_file():
            parts.append(primary)
        return ArchiveParts(primary, parts, missing, "split-zip" if found else "zip")

    match = _RAR_VOLUME.match(name)
    if match or path.suffix.lower() == ".rar":
        base = match.group("base") if match else path.with_suffix("").name
        primary = directory / f"{base}.rar"
        found = {}
        pattern = re.compile(re.escape(base) + r"\.r(\d{2})$", re.I)
        for candidate in directory.iterdir():
            current = pattern.match(candidate.name)
            if current:
                found[int(current.group(1))] = candidate
        parts, missing = _ordered_with_gaps(
            found,
            first=0,
            label=lambda number: f"{base}.r{number:02d}",
        ) if found else ([], [])
        if not primary.is_file():
            missing.append(primary.name)
        if primary.is_file():
            parts.insert(0, primary)
        return ArchiveParts(primary, parts, missing, "old-rar" if found else "rar")

    match = _GENERIC_VOLUME.match(name)
    if match:
        base = match.group("base")
        found = {}
        pattern = re.compile(re.escape(base) + r"\.(\d{3})$", re.I)
        for candidate in directory.iterdir():
            current = pattern.match(candidate.name)
            if current:
                found[int(current.group(1))] = candidate
        parts, missing = _ordered_with_gaps(
            found,
            first=1,
            label=lambda number: f"{base}.{number:03d}",
        )
        primary = found.get(1, directory / f"{base}.001")
        return ArchiveParts(primary, parts, missing, "generic-volume")

    return ArchiveParts(path, [path] if path.is_file() else [], [], "single")


def archive_output_name(path: Path) -> str:
    name = Path(path).name
    for pattern in (
        _PART_RAR,
        _NUMBERED,
        _ZIP_VOLUME,
        _RAR_VOLUME,
        _GENERIC_VOLUME,
    ):
        match = pattern.match(name)
        if match:
            base = match.group("base")
            return Path(base).stem if "." in base else base
    lowered = name.lower()
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"):
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def _safe_entry_path(value: str) -> PurePosixPath:
    cleaned = str(value or "").replace("\\", "/")
    if not cleaned or "\x00" in cleaned:
        raise ArchiveError("Archive contains an empty or invalid path")
    if cleaned.startswith(("/", "//")) or _DRIVE.match(cleaned):
        raise ArchiveError(f"Archive contains an absolute path: {value}")
    parsed = PurePosixPath(cleaned)
    if any(part in {"", ".", ".."} for part in parsed.parts):
        raise ArchiveError(f"Archive contains unsafe traversal: {value}")
    return parsed


def validate_entries(
    entries: Iterable[ArchiveEntry],
    *,
    archive_size: int,
    max_files: int = 100_000,
    max_unpacked_bytes: int = 100 * 1024**3,
    max_ratio: float = 1000.0,
) -> dict:
    items = list(entries)
    if len(items) > max_files:
        raise ArchiveError(f"Archive contains too many entries ({len(items)})")
    total = 0
    encrypted = False
    for entry in items:
        _safe_entry_path(entry.path)
        if entry.link_target:
            raise ArchiveError(f"Archive contains a symbolic link: {entry.path}")
        total += max(0, int(entry.size))
        encrypted = encrypted or entry.encrypted
    if total > max_unpacked_bytes:
        raise ArchiveError(
            f"Archive expands to {total} bytes, above the configured safety limit"
        )
    ratio = total / max(1, int(archive_size or 0))
    if archive_size > 0 and ratio > max_ratio:
        raise ArchiveError(
            f"Archive expansion ratio {ratio:.1f} exceeds the safety limit"
        )
    return {
        "entry_count": len(items),
        "unpacked_bytes": total,
        "expansion_ratio": ratio,
        "encrypted": encrypted,
    }


class SevenZipEngine:
    def __init__(self, binary: str | None = None):
        self.binary = binary or find_7zip()

    @property
    def available(self) -> bool:
        return bool(self.binary and Path(self.binary).is_file() or self.binary and shutil.which(self.binary))

    def _require_binary(self) -> str:
        if not self.binary:
            raise ArchiveError("7-Zip is not available")
        return self.binary

    @staticmethod
    def _password_argument(password: str) -> list[str]:
        return [f"-p{password}"] if password else []

    @staticmethod
    def _raise_for_output(returncode: int, output: str) -> None:
        lowered = output.lower()
        if "wrong password" in lowered or "enter password" in lowered:
            raise ArchivePasswordRequired("Archive password is required or incorrect")
        if returncode != 0:
            tail = "\n".join(output.strip().splitlines()[-12:])
            raise ArchiveError(tail or f"7-Zip exited with code {returncode}")

    def _capture(self, arguments: list[str]) -> str:
        command = [self._require_binary(), *arguments]
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self._raise_for_output(process.returncode, process.stdout)
        return process.stdout

    def list_entries(self, archive: Path, *, password: str = "") -> list[ArchiveEntry]:
        parts = discover_archive_parts(Path(archive))
        if not parts.ready:
            raise ArchiveError("Waiting for archive parts: " + ", ".join(parts.missing))
        output = self._capture(
            ["l", "-slt", "-ba", str(parts.primary), *self._password_argument(password)]
        )
        entries: list[ArchiveEntry] = []
        block: dict[str, str] = {}

        def flush() -> None:
            if not block.get("Path"):
                block.clear()
                return
            path_value = block.get("Path", "")
            entries.append(
                ArchiveEntry(
                    path=path_value,
                    size=int(block.get("Size") or 0),
                    packed_size=int(block.get("Packed Size") or 0),
                    is_directory=(
                        block.get("Folder") == "+"
                        or block.get("Attributes", "").startswith("D")
                    ),
                    encrypted=block.get("Encrypted") == "+",
                    attributes=block.get("Attributes", ""),
                    link_target=(
                        block.get("Symbolic Link")
                        or block.get("Hard Link")
                        or ""
                    ),
                )
            )
            block.clear()

        for raw_line in output.splitlines():
            line = raw_line.rstrip()
            if not line:
                flush()
                continue
            if " = " in line:
                key, value = line.split(" = ", 1)
                block[key.strip()] = value.strip()
        flush()
        return entries

    def inspect(self, archive: Path, *, password: str = "") -> dict:
        parts = discover_archive_parts(Path(archive))
        if not parts.ready:
            return {
                "status": "waiting_input",
                "parts": parts.to_dict(),
                "entries": [],
            }
        entries = self.list_entries(parts.primary, password=password)
        stats = validate_entries(
            entries,
            archive_size=sum(item.stat().st_size for item in parts.parts if item.is_file()),
        )
        return {
            "status": "ready",
            "parts": parts.to_dict(),
            "entries": [item.to_dict() for item in entries],
            **stats,
        }

    def test(self, archive: Path, *, password: str = "") -> dict:
        parts = discover_archive_parts(Path(archive))
        if not parts.ready:
            return {"status": "waiting_input", "parts": parts.to_dict()}
        output = self._capture(
            ["t", str(parts.primary), "-bso1", "-bse1", *self._password_argument(password)]
        )
        return {"status": "ok", "parts": parts.to_dict(), "output": output[-2000:]}

    def extract(
        self,
        archive: Path,
        destination_root: Path,
        *,
        password: str = "",
        delete_archive: bool = False,
        progress: Callable[[float, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        max_files: int = 100_000,
        max_unpacked_bytes: int = 100 * 1024**3,
        max_ratio: float = 1000.0,
    ) -> dict:
        parts = discover_archive_parts(Path(archive))
        if not parts.ready:
            return {"status": "waiting_input", "parts": parts.to_dict()}
        entries = self.list_entries(parts.primary, password=password)
        stats = validate_entries(
            entries,
            archive_size=sum(item.stat().st_size for item in parts.parts if item.is_file()),
            max_files=max_files,
            max_unpacked_bytes=max_unpacked_bytes,
            max_ratio=max_ratio,
        )
        if stats["encrypted"] and not password:
            raise ArchivePasswordRequired("Archive password is required")

        destination_root = Path(destination_root)
        destination_root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(destination_root).free
        if free < stats["unpacked_bytes"]:
            raise ArchiveError(
                f"Not enough disk space: need {stats['unpacked_bytes']} bytes, have {free}"
            )

        staging = destination_root / f".lumi-extract-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        output = destination_root / archive_output_name(parts.primary)
        if output.exists():
            index = 2
            base = output.name
            while output.exists():
                output = destination_root / f"{base} ({index})"
                index += 1

        command = [
            self._require_binary(),
            "x",
            str(parts.primary),
            f"-o{staging}",
            "-y",
            "-bsp1",
            "-bso1",
            "-bb1",
            *self._password_argument(password),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        captured: list[str] = []
        try:
            assert process.stdout is not None
            for line in process.stdout:
                captured.append(line)
                if cancel_event and cancel_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise ArchiveCancelled("Archive extraction was cancelled")
                match = _PERCENT.search(line)
                percent = float(match.group(1)) if match else 0.0
                current = line.strip()
                if progress:
                    progress(percent, current)
            returncode = process.wait()
            combined = "".join(captured)
            self._raise_for_output(returncode, combined)

            root = staging.resolve()
            for extracted in staging.rglob("*"):
                resolved = extracted.resolve(strict=False)
                if root not in resolved.parents and resolved != root:
                    raise ArchiveError("Extracted content escaped the staging directory")
                if extracted.is_symlink():
                    raise ArchiveError(f"Extracted symbolic link is blocked: {extracted.name}")
            os.replace(staging, output)
            if delete_archive:
                for item in parts.parts:
                    item.unlink(missing_ok=True)
            if progress:
                progress(100.0, "Extraction completed")
            return {
                "status": "completed",
                "output_path": str(output),
                "parts": parts.to_dict(),
                **stats,
            }
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
