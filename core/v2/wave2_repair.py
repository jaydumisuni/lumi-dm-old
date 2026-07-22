"""Secure Repair Download Link implementation for Wave 2."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .http_replay import probe_resource
from .models import TaskStatus
from .wave2 import services


def repair_download_link(
    task_id: str,
    envelope_value: dict[str, Any],
) -> dict[str, Any]:
    active = services()
    task = active.runtime.get_task(task_id)
    if task is None:
        raise KeyError(task_id)

    envelope = active.capture(envelope_value)
    if not envelope.url:
        raise ValueError("replacement request requires url")

    if (envelope.method or "GET").upper() == "GET":
        probe = probe_resource(envelope)
        if task.total_bytes and probe.total_bytes and task.total_bytes != probe.total_bytes:
            raise ValueError(
                "Replacement size mismatch: "
                f"expected {task.total_bytes}, got {probe.total_bytes}"
            )
        if task.etag and probe.etag and task.etag != probe.etag:
            raise ValueError("Replacement ETag does not match the existing task")
        envelope.final_url = probe.final_url
    else:
        # POST-generated resources are validated by the transfer response because
        # probing would consume or duplicate the browser request.
        envelope.final_url = envelope.final_url or envelope.url

    task.request = envelope
    task.status = TaskStatus.QUEUED.value
    task.error = ""
    task.error_code = ""
    task.finished_at = ""
    active.runtime.store.save_task(task)
    active.runtime.store.append_event(
        task.id,
        "download_link_repaired",
        {
            "method": envelope.method,
            "final_url": envelope.final_url,
        },
    )
    active.runtime.queue.wake()
    return task.to_dict(public=True)


def repair_from_capture(envelope: dict[str, Any]) -> dict[str, Any]:
    active = services()
    pending = active.get_repair_wait()
    if pending is None:
        raise ValueError("No Lumi task is waiting for a replacement link")
    result = repair_download_link(str(pending["task_id"]), envelope)
    active.clear_repair_wait()
    return result
