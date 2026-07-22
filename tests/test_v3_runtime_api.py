from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading

from core.v2.models import DownloadTask, RequestEnvelope, TaskStatus, TaskType
from core.v2.runtime import LumiRuntime
from core.v2.store import StateStore
from core.v3 import runtime_wave3
from core.v3.runtime_wave3 import Wave3HTTPTransferRunner


def test_verified_http_completion_hands_archive_to_postprocessor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = StateStore(tmp_path / "data")
    partial = tmp_path / "temporary" / "bundle.7z.part"
    final = tmp_path / "downloads" / "bundle.7z"
    partial.parent.mkdir(parents=True)
    final.parent.mkdir(parents=True)
    partial.write_bytes(b"verified archive")
    task = DownloadTask(
        id="archive-http",
        type=TaskType.HTTP.value,
        status=TaskStatus.RUNNING.value,
        request=RequestEnvelope(url="https://example.invalid/bundle.7z"),
        filename=final.name,
        target_dir=str(final.parent),
        temp_dir=str(partial.parent),
        final_path=str(final),
        partial_path=str(partial),
        total_bytes=partial.stat().st_size,
        downloaded_bytes=partial.stat().st_size,
        post_process={"extract": True, "delete_archive": True},
    )
    store.save_task(task)
    submitted = {}

    class FakeController:
        def submit_archive(
            self,
            task_id,
            archive_path,
            destination_root,
            *,
            password="",
            delete_archive=False,
        ):
            submitted.update(
                {
                    "task_id": task_id,
                    "archive_path": Path(archive_path),
                    "destination_root": Path(destination_root),
                    "password": password,
                    "delete_archive": delete_archive,
                }
            )
            return object()

    monkeypatch.setattr(runtime_wave3, "_controller", lambda _store: FakeController())
    runner = Wave3HTTPTransferRunner(
        store,
        task.id,
        pause_event=threading.Event(),
        cancel_event=threading.Event(),
        update_callback=lambda _task: None,
    )

    runner._complete_file(task, partial, final)

    current = store.get_task(task.id)
    assert final.read_bytes() == b"verified archive"
    assert current is not None
    assert current.status == TaskStatus.POST_PROCESSING.value
    assert submitted["task_id"] == task.id
    assert submitted["archive_path"] == final
    assert submitted["delete_archive"] is True
    store.close()


def test_wave3_runtime_dispatches_video_and_torrent_to_new_runners(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeMediaRunner:
        def __init__(self, store, task_id, **_kwargs):
            self.store = store
            self.task_id = task_id

        def run(self):
            task = self.store.get_task(self.task_id)
            task.status = TaskStatus.COMPLETED.value
            self.store.save_task(task)
            calls.append(("media", self.task_id))

    class FakeTorrentRunner:
        def __init__(self, store, _data_dir, task_id, **_kwargs):
            self.store = store
            self.task_id = task_id

        def run(self):
            task = self.store.get_task(self.task_id)
            task.status = TaskStatus.COMPLETED.value
            self.store.save_task(task)
            calls.append(("torrent", self.task_id))

    monkeypatch.setattr(runtime_wave3, "MediaRunner", FakeMediaRunner)
    monkeypatch.setattr(runtime_wave3, "TorrentRunner", FakeTorrentRunner)
    runtime = LumiRuntime(tmp_path / "data")
    runtime.queue.update_queue("default", active=False)
    video = runtime.create_delegated_task(
        TaskType.VIDEO.value,
        "https://media.invalid/watch",
        target_dir=tmp_path / "video",
        metadata={"filename": "video"},
        start_paused=True,
    )
    torrent = runtime.create_delegated_task(
        TaskType.TORRENT.value,
        "magnet:?xt=urn:btih:abc",
        target_dir=tmp_path / "torrent",
        metadata={"filename": "torrent"},
        start_paused=True,
    )

    runtime._run_legacy_backend(video, threading.Event(), threading.Event())
    runtime._run_legacy_backend(torrent, threading.Event(), threading.Event())

    assert calls == [("media", video.id), ("torrent", torrent.id)]
    assert runtime.get_task(video.id).status == TaskStatus.COMPLETED.value
    assert runtime.get_task(torrent.id).status == TaskStatus.COMPLETED.value
    runtime.close()


def test_source_launcher_registers_every_wave3_route(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["LUMIDM_DATA_DIR"] = str(tmp_path / "server-data")
    code = (
        "import server; "
        "routes={rule.rule for rule in server.app.url_map.iter_rules()}; "
        "required={"
        "'/api/v3/media/info','/api/v3/media/start',"
        "'/api/v3/torrent/info','/api/v3/torrent/start',"
        "'/api/v3/archive/inspect','/api/v3/archive/test',"
        "'/api/v3/archive/extract','/api/v3/ffmpeg',"
        "'/api/v3/tasks/<task_id>/postprocess',"
        "'/api/v3/postprocess/<job_id>/cancel'}; "
        "assert required <= routes, required-routes"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
