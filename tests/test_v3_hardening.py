from __future__ import annotations

from pathlib import Path
import threading

from core.v2.models import DownloadTask, RequestEnvelope, TaskStatus, TaskType
from core.v2.store import StateStore
from core.v3 import hardening as _hardening  # activates independent guards
from core.v3.media import MediaRunner
from core.v3 import runtime_wave3
from core.v3.runtime_wave3 import Wave3HTTPTransferRunner


def test_archive_start_failure_keeps_verified_download_completed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = StateStore(tmp_path / "data")
    partial = tmp_path / "temporary" / "package.7z.part"
    final = tmp_path / "downloads" / "package.7z"
    partial.parent.mkdir(parents=True)
    final.parent.mkdir(parents=True)
    partial.write_bytes(b"verified archive payload")
    task = DownloadTask(
        id="archive-start-failure",
        type=TaskType.HTTP.value,
        status=TaskStatus.RUNNING.value,
        request=RequestEnvelope(url="https://example.invalid/package.7z"),
        filename=final.name,
        target_dir=str(final.parent),
        temp_dir=str(partial.parent),
        final_path=str(final),
        partial_path=str(partial),
        total_bytes=partial.stat().st_size,
        downloaded_bytes=partial.stat().st_size,
        post_process={"extract": True},
    )
    store.save_task(task)

    class FailingController:
        def submit_archive(self, *_args, **_kwargs):
            raise RuntimeError("7-Zip binary is unavailable")

    monkeypatch.setattr(
        runtime_wave3,
        "_controller",
        lambda _store: FailingController(),
    )
    runner = Wave3HTTPTransferRunner(
        store,
        task.id,
        pause_event=threading.Event(),
        cancel_event=threading.Event(),
        update_callback=lambda _task: None,
    )

    runner._complete_file(task, partial, final)

    completed = store.get_task(task.id)
    assert completed is not None
    assert completed.status == TaskStatus.COMPLETED.value
    assert final.read_bytes() == b"verified archive payload"
    assert completed.post_process["status"] == "failed_to_start"
    assert "7-Zip" in completed.post_process["warning"]
    assert completed.metadata["completion_warning"].startswith(
        "Download completed"
    )
    events = store.list_events(task.id)
    assert events[0]["event_type"] == "archive_postprocess_start_failed"
    store.close()


def test_media_output_fallback_finds_merged_or_converted_file(tmp_path: Path) -> None:
    output = tmp_path / "Lumi Video [video-42].mp3"
    output.write_bytes(b"converted output")
    (tmp_path / "Lumi Video [video-42].jpg").write_bytes(b"thumbnail")
    (tmp_path / "Lumi Video [video-42].en.vtt").write_text(
        "WEBVTT",
        encoding="utf-8",
    )
    missing_stream = tmp_path / "Lumi Video [video-42].f251.webm"
    info = {
        "id": "video-42",
        "title": "Lumi Video",
        "requested_downloads": [{"filepath": str(missing_stream)}],
    }

    paths = MediaRunner._final_paths(info)

    assert paths == [output]
