from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading

from core.v2.archives import (
    ArchiveLimits,
    ArchiveSecurityError,
    ArchiveService,
    group_multipart,
)
from core.v2.media import MediaService
import core.v2.media as media_module
from core.v2.models import DownloadTask, RequestEnvelope, TaskStatus, TaskType
from core.v2.postprocess import FFmpegPlan, FFmpegService, PostProcessController
from core.v2.store import StateStore
from core.v2.torrents import TorrentPlan, TorrentService, bencode, inspect_torrent
import core.v2.torrents as torrent_module


def executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def task_for(
    tmp_path: Path,
    task_id: str,
    task_type: str,
    *,
    source: str,
    final_path: Path | None = None,
    metadata: dict | None = None,
) -> DownloadTask:
    target = tmp_path / "downloads"
    target.mkdir(parents=True, exist_ok=True)
    final = final_path or target / f"{task_id}.bin"
    return DownloadTask(
        id=task_id,
        type=task_type,
        status=TaskStatus.PAUSED.value,
        request=RequestEnvelope(url=source),
        filename=final.name,
        target_dir=str(target),
        temp_dir=str(tmp_path / "temporary"),
        final_path=str(final),
        partial_path=str(tmp_path / "temporary" / f"{final.name}.part"),
        metadata=metadata or {},
    )


def test_torrent_metadata_supports_file_selection_and_info_hash(tmp_path: Path) -> None:
    info = {
        b"name": b"Lumi Pack",
        b"piece length": 16384,
        b"pieces": b"0" * 20,
        b"files": [
            {b"length": 4, b"path": [b"docs", b"one.txt"]},
            {b"length": 6, b"path": [b"video", b"two.mp4"]},
        ],
    }
    torrent = {
        b"announce": b"https://tracker.example/announce",
        b"info": info,
    }
    path = tmp_path / "sample.torrent"
    path.write_bytes(bencode(torrent))

    result = inspect_torrent(str(path))

    assert result["name"] == "Lumi Pack"
    assert result["total_size"] == 10
    assert [item["path"] for item in result["files"]] == [
        "docs/one.txt",
        "video/two.mp4",
    ]
    assert len(result["info_hash"]) == 40
    assert result["trackers"] == ["https://tracker.example/announce"]


def test_multipart_group_reports_missing_volume(tmp_path: Path) -> None:
    first = tmp_path / "backup.part1.rar"
    third = tmp_path / "backup.part3.rar"
    first.write_bytes(b"one")
    third.write_bytes(b"three")

    result = group_multipart(first)

    assert result["multipart"] is True
    assert result["complete"] is False
    assert result["missing"] == [2]


def test_archive_validation_blocks_traversal_and_bombs(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    archive.write_bytes(b"x")
    service = ArchiveService(binary="unused")

    unsafe = {
        "entries": [
            {
                "path": "../escape.exe",
                "size": 10,
                "packed_size": 1,
                "folder": False,
                "encrypted": False,
                "attributes": "A",
                "method": "Deflate",
                "crc": "",
            }
        ],
        "packed_bytes": 1,
    }
    try:
        service.validate(unsafe, archive_path=archive)
    except ArchiveSecurityError as exc:
        assert "Unsafe archive path" in str(exc)
    else:
        raise AssertionError("Traversal archive was accepted")

    bomb = {
        "entries": [
            {
                "path": "large.bin",
                "size": 50_000,
                "packed_size": 1,
                "folder": False,
                "encrypted": False,
                "attributes": "A",
                "method": "Deflate",
                "crc": "",
            }
        ],
        "packed_bytes": 1,
    }
    try:
        service.validate(
            bomb,
            archive_path=archive,
            limits=ArchiveLimits(max_ratio=10),
        )
    except ArchiveSecurityError as exc:
        assert "expansion ratio" in str(exc)
    else:
        raise AssertionError("Archive bomb ratio was accepted")


def test_fake_7zip_lists_tests_and_extracts_through_staging(tmp_path: Path) -> None:
    fake = executable(
        tmp_path / "7zz",
        """#!/usr/bin/env python3
import pathlib, sys
args = sys.argv[1:]
command = next((item for item in args if item in {'l','t','x'}), '')
if command == 'l':
    print('Path = archive.zip')
    print('Type = zip')
    print()
    print('----------')
    print('Path = folder/file.txt')
    print('Size = 5')
    print('Packed Size = 5')
    print('Folder = -')
    print('Encrypted = -')
    print('Attributes = A')
    print('Method = Store')
    print()
    raise SystemExit(0)
if command == 't':
    print('Everything is Ok')
    raise SystemExit(0)
if command == 'x':
    output = next(item[2:] for item in args if item.startswith('-o'))
    target = pathlib.Path(output) / 'folder'
    target.mkdir(parents=True, exist_ok=True)
    (target / 'file.txt').write_text('hello', encoding='utf-8')
    print('50%')
    print('100%')
    raise SystemExit(0)
raise SystemExit(2)
""",
    )
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"fake")
    service = ArchiveService(binary=str(fake))

    listing = service.list(archive)
    tested = service.test(archive)
    progress = []
    extracted = service.extract(
        archive,
        tmp_path / "output",
        progress_callback=progress.append,
    )

    assert listing["status"] == "ok"
    assert listing["entries"][0]["path"] == "folder/file.txt"
    assert tested["status"] == "ok"
    assert extracted["status"] == "completed"
    assert (tmp_path / "output" / "folder" / "file.txt").read_text() == "hello"
    assert progress == [50, 100]
    assert not list(tmp_path.glob(".lumi-extract-*"))


def test_ffmpeg_conversion_reports_progress_and_commits_atomically(tmp_path: Path) -> None:
    ffprobe = executable(
        tmp_path / "ffprobe",
        """#!/usr/bin/env python3
print('{\"format\": {\"duration\": \"2.0\"}}')
""",
    )
    ffmpeg = executable(
        tmp_path / "ffmpeg",
        """#!/usr/bin/env python3
import pathlib, shutil, sys
args = sys.argv[1:]
source = pathlib.Path(args[args.index('-i') + 1])
destination = pathlib.Path(args[-1])
destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(source, destination)
print('out_time_ms=1000000')
print('progress=continue')
print('out_time_ms=2000000')
print('progress=end')
""",
    )
    source = tmp_path / "source.mkv"
    source.write_bytes(b"media")
    destination = tmp_path / "final.mp4"
    service = FFmpegService(ffmpeg=str(ffmpeg), ffprobe=str(ffprobe))
    progress = []

    result = service.convert(
        source,
        destination,
        FFmpegPlan(output_container="mp4"),
        cancel_event=threading.Event(),
        progress_callback=progress.append,
    )

    assert result["status"] == "completed"
    assert destination.read_bytes() == b"media"
    assert progress[-1] == 100.0
    assert not list(tmp_path.glob("*.lumi-processing*"))


class FakeYoutubeDL:
    last_options: dict = {}

    def __init__(self, options):
        type(self).last_options = options
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, url, download=False):
        if not download:
            return {
                "id": "abc",
                "title": "Lumi Video",
                "uploader": "TechGuy",
                "duration": 30,
                "formats": [
                    {
                        "format_id": "137",
                        "format_note": "1080p",
                        "ext": "mp4",
                        "height": 1080,
                        "vcodec": "avc1",
                        "acodec": "none",
                        "filesize": 100,
                        "tbr": 5000,
                    },
                    {
                        "format_id": "140",
                        "format_note": "audio",
                        "ext": "m4a",
                        "vcodec": "none",
                        "acodec": "mp4a",
                        "filesize": 10,
                        "abr": 128,
                    },
                ],
                "subtitles": {"en": [{}]},
                "automatic_captions": {"bem": [{}]},
            }
        target = Path(self.options["outtmpl"]).parent / "Lumi Video [abc].mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video")
        hook = self.options["progress_hooks"][0]
        hook(
            {
                "status": "downloading",
                "filename": str(target),
                "downloaded_bytes": 50,
                "total_bytes": 100,
                "speed": 25,
                "eta": 2,
            }
        )
        hook({"status": "finished", "filename": str(target)})
        return {"id": "abc", "title": "Lumi Video", "ext": "mp4", "_filename": str(target)}

    def prepare_filename(self, result):
        return result["_filename"]


class FakeYTDLPModule:
    YoutubeDL = FakeYoutubeDL


def test_media_resolution_and_transfer_use_real_task_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(media_module, "_yt_dlp", FakeYTDLPModule)
    store = StateStore(tmp_path / "data")
    service = MediaService(store)
    info = service.resolve("https://video.example/watch/abc")

    assert info["title"] == "Lumi Video"
    assert info["formats"][0]["height"] == 1080
    assert info["subtitles"] == ["en"]
    assert info["automatic_captions"] == ["bem"]

    task = task_for(
        tmp_path,
        "video-task",
        TaskType.VIDEO.value,
        source="https://video.example/watch/abc",
        metadata={
            "format_id": "137+140",
            "subtitles": True,
            "subtitle_languages": ["en", "bem"],
            "thumbnail": True,
            "embed_metadata": True,
        },
    )
    store.save_task(task)
    completed = service.run(task, threading.Event(), threading.Event())

    assert completed.status == TaskStatus.COMPLETED.value
    assert completed.progress_percent == 100.0
    assert Path(completed.final_path).read_bytes() == b"video"
    assert FakeYoutubeDL.last_options["format"] == "137+140"
    assert FakeYoutubeDL.last_options["subtitleslangs"] == ["en", "bem"]
    store.close()


def test_fake_aria2_receives_file_selection_and_seed_controls(tmp_path: Path, monkeypatch) -> None:
    arguments = tmp_path / "aria-args.json"
    aria = executable(
        tmp_path / "aria2c",
        f"""#!/usr/bin/env python3
import json, pathlib, sys
pathlib.Path({str(arguments)!r}).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')
print('[#1 SIZE:10B/10B(100%) CN:1 SEED:3 DL:1MiB]')
""",
    )
    monkeypatch.setattr(torrent_module, "_lt", None)
    monkeypatch.setattr(torrent_module, "find_aria2c", lambda: str(aria))
    store = StateStore(tmp_path / "data")
    service = TorrentService(store)
    task = task_for(
        tmp_path,
        "torrent-task",
        TaskType.TORRENT.value,
        source="magnet:?xt=urn:btih:abc&dn=Lumi",
        metadata={
            "selected_file_indexes": [0, 2],
            "ratio_limit": 1.5,
            "seed_time_minutes": 20,
            "stop_after_download": False,
        },
    )
    store.save_task(task)

    completed = service.run(task, threading.Event(), threading.Event())
    args = json.loads(arguments.read_text())

    assert completed.status == TaskStatus.COMPLETED.value
    assert "--select-file=1,3" in args
    assert "--seed-ratio=1.5" in args
    assert "--seed-time=20" in args
    assert completed.metadata["seeders"] == 3
    store.close()


def test_wave3_routes_are_registered_from_source(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["LUMIDM_DATA_DIR"] = str(tmp_path / "server-data")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import server; "
                "routes={rule.rule for rule in server.app.url_map.iter_rules()}; "
                "required={'/api/media/resolve','/api/torrents/inspect',"
                "'/api/archives/list','/api/archives/test',"
                "'/api/downloads/<task_id>/post-process/run'}; "
                "assert required <= routes, required-routes"
            ),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
