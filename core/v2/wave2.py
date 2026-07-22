"""Wave 2 application services: organisation, capture and resolution."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import time
from typing import Any

from .categories import CategoryManager, CategoryRule
from .host_profiles import HostProfile, HostProfileManager
from .models import RequestEnvelope, TaskStatus, TaskType
from .resolvers import default_registry
from .runtime import _require_runtime
from .vault import secure_request_envelope


_REPAIR_WAIT_KEY = "browser.repair_wait.v2"


class Wave2Services:
    def __init__(self):
        runtime = _require_runtime()
        self.runtime = runtime
        self.categories = CategoryManager(runtime.store)
        self.host_profiles = HostProfileManager(runtime.store, runtime.data_dir)
        self.resolvers = default_registry()

    def capture(self, envelope: dict[str, Any]) -> RequestEnvelope:
        secured = secure_request_envelope(self.runtime.data_dir, envelope)
        return RequestEnvelope.from_dict(secured)

    def resolve(self, envelope: dict[str, Any]) -> dict[str, Any]:
        secured = self.capture(envelope)
        return self.resolvers.resolve(secured).to_dict(public=True)

    def start_http(
        self,
        url: str,
        *,
        target_dir: Path,
        filename: str = "",
        overwrite: bool = False,
        resume: bool = True,
        connections: int = 0,
        max_speed_bps: int = 0,
        queue_id: str = "default",
        priority: int = 0,
        start_paused: bool = False,
        request_envelope: dict[str, Any] | None = None,
        temp_dir: Path | None = None,
        duplicate_policy: str = "",
        category_id: str = "",
    ) -> dict[str, Any]:
        envelope_data = dict(request_envelope or {})
        envelope_data.setdefault("url", url)
        envelope_data.setdefault("suggested_filename", filename)
        envelope = self.capture(envelope_data)

        effective_connections = max(
            1,
            int(connections or self.runtime.default_connections),
        )
        envelope, effective_connections, max_speed_bps, host_profile = (
            self.host_profiles.apply(
                envelope,
                connections=effective_connections,
                speed_limit_bps=max_speed_bps,
            )
        )
        # Host credentials must leave the task database immediately.
        envelope = self.capture(asdict(envelope))

        suggested = (
            filename
            or envelope.suggested_filename
            or Path(url.split("?", 1)[0]).name
            or "download.bin"
        )
        decision = self.categories.resolve(
            filename=suggested,
            url=url,
            base_dir=Path(target_dir),
            temp_base_dir=Path(temp_dir or target_dir),
            fixed_category=category_id,
        )

        policy = duplicate_policy or ("overwrite" if overwrite else "rename")
        existing = [
            task
            for task in self.runtime.list_tasks(5000)
            if task.request.url == url
            and task.status not in {
                TaskStatus.CANCELLED.value,
                TaskStatus.FAILED.value,
            }
        ]
        if existing and policy == "reuse":
            return existing[0].to_dict(public=True)
        if existing and policy == "reject":
            raise FileExistsError(
                f"An existing Lumi task already uses this URL: {existing[0].id}"
            )

        task = self.runtime.create_http_task(
            url,
            target_dir=decision.target_dir,
            temp_dir=decision.temp_dir,
            filename=suggested,
            overwrite=overwrite,
            resume=resume,
            connections=effective_connections,
            max_speed_bps=max_speed_bps,
            queue_id=queue_id,
            priority=priority,
            start_paused=start_paused,
            request_envelope=envelope,
            duplicate_policy=(
                "overwrite" if policy == "overwrite" else "rename"
            ),
        )
        task.category_id = decision.category_id
        task.host_profile_id = host_profile.id if host_profile else ""
        if decision.auto_extract:
            task.post_process["extract"] = True
        if decision.completion_action != "none":
            task.post_process["completion_action"] = decision.completion_action
        self.runtime.store.save_task(task)
        return task.to_dict(public=True)

    def start_delegated(
        self,
        task_type: str,
        url: str,
        *,
        target_dir: Path,
        metadata: dict[str, Any],
        queue_id: str,
        priority: int,
        start_paused: bool,
        category_id: str = "",
    ) -> dict[str, Any]:
        suggested = str(metadata.get("filename") or "download")
        decision = self.categories.resolve(
            filename=suggested,
            url=url,
            base_dir=Path(target_dir),
            temp_base_dir=Path(target_dir),
            fixed_category=category_id,
        )
        task = self.runtime.create_delegated_task(
            task_type,
            url,
            target_dir=decision.target_dir,
            metadata=metadata,
            queue_id=queue_id,
            priority=priority,
            start_paused=start_paused,
        )
        task.category_id = decision.category_id
        self.runtime.store.save_task(task)
        return task.to_dict(public=True)

    def set_repair_wait(
        self,
        task_id: str,
        *,
        original_page: str = "",
        expires_in: int = 600,
    ) -> dict[str, Any]:
        task = self.runtime.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task.status not in {
            TaskStatus.NEEDS_LINK.value,
            TaskStatus.PAUSED.value,
            TaskStatus.FAILED.value,
        }:
            raise ValueError("Pause the task or wait for a link error before repair")
        pending = {
            "task_id": task_id,
            "filename": task.filename,
            "expected_size": task.total_bytes,
            "original_page": original_page or task.request.original_page,
            "expires_at": int(time.time()) + max(60, min(3600, int(expires_in))),
        }
        self.runtime.store.set_setting(_REPAIR_WAIT_KEY, pending)
        self.runtime.store.append_event(task_id, "repair_capture_waiting", pending)
        return pending

    def get_repair_wait(self) -> dict[str, Any] | None:
        pending = self.runtime.store.get_setting(_REPAIR_WAIT_KEY)
        if not isinstance(pending, dict):
            return None
        if int(pending.get("expires_at") or 0) <= int(time.time()):
            self.runtime.store.set_setting(_REPAIR_WAIT_KEY, None)
            return None
        if self.runtime.get_task(str(pending.get("task_id") or "")) is None:
            self.runtime.store.set_setting(_REPAIR_WAIT_KEY, None)
            return None
        return pending

    def clear_repair_wait(self) -> None:
        self.runtime.store.set_setting(_REPAIR_WAIT_KEY, None)

    def repair_from_capture(self, envelope: dict[str, Any]) -> dict[str, Any]:
        pending = self.get_repair_wait()
        if pending is None:
            raise ValueError("No Lumi task is waiting for a replacement link")
        secured = self.capture(envelope)
        task = self.runtime.repair_link(
            str(pending["task_id"]),
            asdict(secured),
        )
        self.clear_repair_wait()
        return task.to_dict(public=True)


_SERVICES: Wave2Services | None = None


def services() -> Wave2Services:
    global _SERVICES
    runtime = _require_runtime()
    if _SERVICES is None or _SERVICES.runtime is not runtime:
        _SERVICES = Wave2Services()
    return _SERVICES


def start_http(
    url: str,
    *,
    target_dir: Path,
    filename: str = "",
    overwrite: bool = False,
    resume: bool = True,
    connections: int = 0,
    max_speed_bps: int = 0,
    queue_id: str = "default",
    priority: int = 0,
    start_paused: bool = False,
    request_envelope: dict[str, Any] | None = None,
    temp_dir: Path | None = None,
    duplicate_policy: str = "",
    category_id: str = "",
) -> dict[str, Any]:
    return services().start_http(
        url,
        target_dir=target_dir,
        filename=filename,
        overwrite=overwrite,
        resume=resume,
        connections=connections,
        max_speed_bps=max_speed_bps,
        queue_id=queue_id,
        priority=priority,
        start_paused=start_paused,
        request_envelope=request_envelope,
        temp_dir=temp_dir,
        duplicate_policy=duplicate_policy,
        category_id=category_id,
    )


def start_torrent(
    url: str,
    *,
    target_dir: Path,
    connections: int = 0,
    queue_id: str = "default",
    priority: int = 0,
    start_paused: bool = False,
    category_id: str = "",
) -> dict[str, Any]:
    return services().start_delegated(
        TaskType.TORRENT.value,
        url,
        target_dir=target_dir,
        metadata={
            "filename": (
                url[:60] if url.startswith("magnet:") else Path(url).name
            ),
            "connections": connections,
        },
        queue_id=queue_id,
        priority=priority,
        start_paused=start_paused,
        category_id=category_id,
    )


def start_video(
    url: str,
    *,
    target_dir: Path,
    format_id: str = "bestvideo+bestaudio/best",
    audio_only: bool = False,
    subtitles: bool = False,
    queue_id: str = "default",
    priority: int = 0,
    start_paused: bool = False,
    category_id: str = "video",
) -> dict[str, Any]:
    return services().start_delegated(
        TaskType.VIDEO.value,
        url,
        target_dir=target_dir,
        metadata={
            "filename": "Fetching title…",
            "format_id": format_id,
            "audio_only": audio_only,
            "subtitles": subtitles,
        },
        queue_id=queue_id,
        priority=priority,
        start_paused=start_paused,
        category_id=category_id,
    )


def secure_capture(envelope: dict[str, Any]) -> dict[str, Any]:
    return services().capture(envelope).redacted_dict()


def resolve_source(envelope: dict[str, Any]) -> dict[str, Any]:
    return services().resolve(envelope)


def list_categories() -> list[dict[str, Any]]:
    return [item.to_dict() for item in services().categories.list()]


def save_category(value: dict[str, Any]) -> dict[str, Any]:
    return services().categories.save(CategoryRule.from_dict(value)).to_dict()


def delete_category(category_id: str) -> None:
    services().categories.delete(category_id)


def list_host_profiles() -> list[dict[str, Any]]:
    return [item.to_dict(public=True) for item in services().host_profiles.list()]


def save_host_profile(
    value: dict[str, Any],
    *,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    return services().host_profiles.save(
        HostProfile.from_dict(value),
        username=username,
        password=password,
    ).to_dict(public=True)


def delete_host_profile(profile_id: str) -> None:
    services().host_profiles.delete(profile_id)


def host_intercept_mode(url: str) -> str:
    profile = services().host_profiles.match_url(url)
    return profile.intercept_mode if profile else "auto"


def set_repair_wait(
    task_id: str,
    *,
    original_page: str = "",
    expires_in: int = 600,
) -> dict[str, Any]:
    return services().set_repair_wait(
        task_id,
        original_page=original_page,
        expires_in=expires_in,
    )


def get_repair_wait() -> dict[str, Any] | None:
    return services().get_repair_wait()


def clear_repair_wait() -> None:
    services().clear_repair_wait()


def repair_from_capture(envelope: dict[str, Any]) -> dict[str, Any]:
    return services().repair_from_capture(envelope)
