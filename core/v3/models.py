"""Domain models for Lumi media, torrent and post-processing work."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
import uuid

from core.v2.models import utc_now


class PostProcessStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    PASSWORD_REQUIRED = "password_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ArchiveEntry:
    path: str
    size: int = 0
    packed_size: int = 0
    is_directory: bool = False
    encrypted: bool = False
    attributes: str = ""
    link_target: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PostProcessJob:
    kind: str
    task_id: str
    input_paths: list[str]
    output_path: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = PostProcessStatus.QUEUED.value
    progress_percent: float = 0.0
    current_item: str = ""
    error: str = ""
    created_at: str = field(default_factory=utc_now)
    started_at: str = ""
    finished_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PostProcessJob":
        return cls(
            id=str(value.get("id") or uuid.uuid4().hex),
            kind=str(value.get("kind") or "unknown"),
            task_id=str(value.get("task_id") or ""),
            input_paths=[str(item) for item in value.get("input_paths") or []],
            output_path=str(value.get("output_path") or ""),
            status=str(value.get("status") or PostProcessStatus.QUEUED.value),
            progress_percent=float(value.get("progress_percent") or 0.0),
            current_item=str(value.get("current_item") or ""),
            error=str(value.get("error") or ""),
            created_at=str(value.get("created_at") or utc_now()),
            started_at=str(value.get("started_at") or ""),
            finished_at=str(value.get("finished_at") or ""),
            metadata=dict(value.get("metadata") or {}),
        )
