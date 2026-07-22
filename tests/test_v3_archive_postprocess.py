from __future__ import annotations

from pathlib import Path
import os
import stat
import time

import pytest

from core.v2.models import DownloadTask, RequestEnvelope, TaskStatus
from core.v2.store import StateStore
from core.v3.archive import (
    ArchiveError,
    SevenZipEngine,
    discover_archive_parts,
    validate_entries,
)
from core.v3.models import ArchiveEntry, PostProcessStatus
from core.v3.postprocess import PostProcessController


def _executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _fake_7zip(tmp_path: Path, *, unsafe: bool = False) -> Path:
    entry = "../escape.txt" if unsafe else "folder/file.txt"
    return _executable(
        tmp_path / ("fake-7z-unsafe" if unsafe else "fake-7z"),
        f"""#!/usr/bin/env python3
import pathlib
import sys
args = sys.argv[1:]
mode = args[0]
if mode == 'l':
    print('Path = {entry}')
    print('Size = 11')
    print('Packed Size = 8')
    print('Attributes = A')
    print('Encrypted = -')
    print()
    raise SystemExit(0)
if mode == 't':
    print('Everything is Ok')
    raise SystemExit(0)
if mode == 'x':
    output = next(item[2:] for item in args if item.startswith('-o'))
    target = pathlib.Path(output) / 'folder'
    target.mkdir(parents=True, exist_ok=True)
    (target / 'file.txt').write_text('hello lumi!', encoding='utf-8')
    print('50% folder/file.txt', flush=True)
    print('100% Everything is Ok', flush=True)
    raise SystemExit(0)
raise SystemExit(2)
""",
    )


def _fake_ffmpeg(tmp_path: Path) -> Path:
    return _executable(
        tmp_path / "fake-ffmpeg",
        """#!/usr/bin/env python3
import pathlib
import sys
output = pathlib.Path(sys.argv[-1])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_bytes(b'ffmpeg-output')
print('out_time_ms=1000000', flush=True)
print('progress=end', flush=True)
""",
    )


def _task(store: StateStore, tmp_path: Path, task_id: str) -> DownloadTask:
    source = tmp_path / f"{task_id}.bin"
    source.write_bytes(b"input")
    task = DownloadTask(
        id=task_id,
        type="provider",
        status=TaskStatus.PAUSED.value,
        request=RequestEnvelope(url=source.resolve().as_uri()),
        filename=source.name,
        target_dir=str(tmp_path / "output"),
        temp_dir=str(tmp_path / "temporary"),
        final_path=str(source),
        partial_path="",
    )
    store.save_task(task)
    return task


def _wait_job(controller: PostProcessController, task_id: str, job_id: str):
    deadline = time.time() + 8
    while time.time() < deadline:
        job = controller.get_job(task_id, job_id)
        if job and job.status in {
            PostProcessStatus.COMPLETED.value,
            PostProcessStatus.FAILED.value,
            PostProcessStatus.CANCELLED.value,
            PostProcessStatus.PASSWORD_REQUIRED.value,
            PostProcessStatus.WAITING_INPUT.value,
        }:
            return job
        time.sleep(0.05)
    raise AssertionError("post-processing job did not finish")


def test_multipart_archive_reports_missing_gap(tmp_path: Path) -> None:
    first = tmp_path / "bundle.part1.rar"
    third = tmp_path / "bundle.part3.rar"
    first.write_bytes(b"one")
    third.write_bytes(b"three")

    parts = discover_archive_parts(first)

    assert not parts.ready
    assert parts.missing == ["bundle.part2.rar"]
    assert [item.name for item in parts.parts] == [
        "bundle.part1.rar",
        "bundle.part3.rar",
    ]


def test_archive_validation_blocks_traversal_links_and_bombs() -> None:
    with pytest.raises(ArchiveError, match="unsafe traversal"):
        validate_entries(
            [ArchiveEntry(path="../escape.txt", size=1)],
            archive_size=10,
        )
    with pytest.raises(ArchiveError, match="symbolic link"):
        validate_entries(
            [ArchiveEntry(path="safe/link", size=0, link_target="../../target")],
            archive_size=10,
        )
    with pytest.raises(ArchiveError, match="expansion ratio"):
        validate_entries(
            [ArchiveEntry(path="huge.bin", size=50_000)],
            archive_size=1,
            max_ratio=100,
        )


def test_fake_7zip_inspect_test_and_secure_extract(tmp_path: Path) -> None:
    archive = tmp_path / "bundle.7z"
    archive.write_bytes(b"archive")
    engine = SevenZipEngine(str(_fake_7zip(tmp_path)))
    progress: list[float] = []

    inspected = engine.inspect(archive)
    tested = engine.test(archive)
    extracted = engine.extract(
        archive,
        tmp_path / "extracted",
        progress=lambda percent, _current: progress.append(percent),
    )

    assert inspected["status"] == "ready"
    assert inspected["entries"][0]["path"] == "folder/file.txt"
    assert tested["status"] == "ok"
    output = Path(extracted["output_path"])
    assert (output / "folder" / "file.txt").read_text() == "hello lumi!"
    assert progress[-1] == 100.0


def test_unsafe_7zip_listing_is_rejected_before_extraction(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.7z"
    archive.write_bytes(b"archive")
    engine = SevenZipEngine(str(_fake_7zip(tmp_path, unsafe=True)))

    with pytest.raises(ArchiveError, match="unsafe traversal"):
        engine.inspect(archive)


def test_archive_and_ffmpeg_jobs_persist_into_task_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    archive_task = _task(store, tmp_path, "archive-task")
    archive = tmp_path / "payload.7z"
    archive.write_bytes(b"archive")
    controller = PostProcessController(
        store,
        archive_engine=SevenZipEngine(str(_fake_7zip(tmp_path))),
        ffmpeg_binary=str(_fake_ffmpeg(tmp_path)),
    )

    archive_job = controller.submit_archive(
        archive_task.id,
        archive,
        tmp_path / "archive-output",
    )
    finished_archive = _wait_job(controller, archive_task.id, archive_job.id)

    assert finished_archive.status == PostProcessStatus.COMPLETED.value
    assert store.get_task(archive_task.id).status == TaskStatus.COMPLETED.value

    media_task = _task(store, tmp_path, "media-task")
    video = tmp_path / "video.bin"
    audio = tmp_path / "audio.bin"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    output = tmp_path / "media-output" / "merged.mp4"
    ffmpeg_job = controller.submit_ffmpeg(
        media_task.id,
        [video, audio],
        output,
        mode="merge",
        duration_seconds=1,
    )
    finished_ffmpeg = _wait_job(controller, media_task.id, ffmpeg_job.id)

    assert finished_ffmpeg.status == PostProcessStatus.COMPLETED.value
    assert output.read_bytes() == b"ffmpeg-output"
    completed_task = store.get_task(media_task.id)
    assert completed_task.status == TaskStatus.COMPLETED.value
    assert completed_task.final_path == str(output)
    controller.close()
    store.close()
