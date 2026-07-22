from __future__ import annotations

import json
from pathlib import Path
import time

from core.v2.models import DownloadTask, RequestEnvelope, TaskStatus, TaskType
from core.v2.queueing import QueueController
from core.v2.store import StateStore


def make_task(task_id: str, *, status: str, priority: int = 0) -> DownloadTask:
    return DownloadTask(
        id=task_id,
        type=TaskType.HTTP.value,
        status=status,
        request=RequestEnvelope(url=f"https://example.invalid/{task_id}"),
        filename=f"{task_id}.bin",
        target_dir=".",
        temp_dir=".",
        final_path=f"{task_id}.bin",
        partial_path=f"{task_id}.bin.part",
        priority=priority,
    )


def test_request_envelope_redacts_secrets() -> None:
    envelope = RequestEnvelope(
        url="https://example.invalid/file",
        headers={
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "Referer": "https://example.invalid/",
        },
        cookie_reference="vault:item",
        post_body_reference="vault:post",
    )

    public = envelope.redacted_dict()

    assert public["headers"]["Authorization"] == "<redacted>"
    assert public["headers"]["Cookie"] == "<redacted>"
    assert public["headers"]["Referer"] == "https://example.invalid/"
    assert public["cookie_reference"] == "<secure-reference>"
    assert public["post_body_reference"] == "<secure-reference>"


def test_store_persists_and_recovers_active_tasks(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    task = make_task("recover-me", status=TaskStatus.RUNNING.value)
    store.save_task(task)

    assert store.get_task(task.id).status == TaskStatus.RUNNING.value
    assert store.recover_incomplete() == 1

    recovered = store.get_task(task.id)
    assert recovered is not None
    assert recovered.status == TaskStatus.PAUSED.value
    store.close()

    reopened = StateStore(tmp_path)
    again = reopened.get_task(task.id)
    assert again is not None
    assert again.status == TaskStatus.PAUSED.value
    reopened.close()


def test_resume_journal_is_atomic_and_readable(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    payload = {
        "schema_version": 2,
        "task_id": "abc",
        "segments": [{"start": 0, "end": 99, "downloaded": 50}],
    }

    store.save_resume("abc", payload)

    assert store.load_resume("abc") == payload
    assert not list(store.resume_dir.glob("*.tmp"))
    store.close()


def test_queue_starts_high_priority_first(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    started: list[str] = []
    low = make_task("low", status=TaskStatus.QUEUED.value, priority=1)
    high = make_task("high", status=TaskStatus.QUEUED.value, priority=10)
    store.save_task(low)
    store.save_task(high)

    controller = QueueController(
        store,
        lambda task_id: started.append(task_id),
        max_running=1,
        poll_interval=0.02,
    )
    controller.wake()

    deadline = time.time() + 2
    while not started and time.time() < deadline:
        time.sleep(0.02)

    assert started == ["high"]

    controller.task_finished("high", "default")
    high.status = TaskStatus.COMPLETED.value
    store.save_task(high)
    controller.wake()

    deadline = time.time() + 2
    while len(started) < 2 and time.time() < deadline:
        time.sleep(0.02)

    assert started == ["high", "low"]
    controller.close()
    store.close()


def test_legacy_json_import_marks_active_tasks_paused(tmp_path: Path) -> None:
    legacy = tmp_path / "downloads.json"
    legacy.write_text(
        json.dumps(
            {
                "old": {
                    "id": "old",
                    "type": "http",
                    "status": "running",
                    "url": "https://example.invalid/file",
                    "filename": "file.bin",
                    "target_dir": str(tmp_path),
                    "path": str(tmp_path / "file.bin"),
                    "partial_path": str(tmp_path / "file.bin.part"),
                }
            }
        ),
        encoding="utf-8",
    )
    store = StateStore(tmp_path / "data")

    assert store.import_legacy_json(legacy) == 1

    task = store.get_task("old")
    assert task is not None
    assert task.status == TaskStatus.PAUSED.value
    store.close()
