"""Independent Wave 3 hardening guards.

These guards preserve two important truth boundaries:

* a verified HTTP download remains completed even when optional archive
  post-processing cannot start;
* yt-dlp completion resolves the real merged/converted output even when its
  returned requested-download path points at a stream that FFmpeg removed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.v2.models import TaskStatus

from .media import MediaRunner
from . import runtime_wave3


_SIDECAR_SUFFIXES = {
    ".part",
    ".ytdl",
    ".json",
    ".description",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".vtt",
    ".srt",
    ".ass",
    ".lrc",
}


def _candidate_values(entry: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for candidate in [*(entry.get("requested_downloads") or []), entry]:
        if not isinstance(candidate, dict):
            continue
        value = candidate.get("filepath") or candidate.get("_filename")
        if value:
            values.append(str(value))
    return values


def _install_media_output_guard() -> None:
    if getattr(MediaRunner, "_lumi_output_guard", False):
        return
    original = MediaRunner._final_paths

    def final_paths(info: dict[str, Any]) -> list[Path]:
        paths = list(original(info))
        seen = {item.resolve() for item in paths if item.exists()}
        entries = list(info.get("entries") or [info])

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            media_id = str(entry.get("id") or "").strip()
            parents: set[Path] = set()
            for value in _candidate_values(entry):
                parents.add(Path(value).expanduser().parent)

            for parent in parents:
                if not parent.is_dir():
                    continue
                candidates: list[Path] = []
                for candidate in parent.iterdir():
                    if not candidate.is_file():
                        continue
                    lowered = candidate.name.lower()
                    if any(lowered.endswith(suffix) for suffix in _SIDECAR_SUFFIXES):
                        continue
                    if media_id and f"[{media_id}]" not in candidate.name:
                        continue
                    candidates.append(candidate)

                candidates.sort(
                    key=lambda item: (item.stat().st_mtime_ns, item.stat().st_size),
                    reverse=True,
                )
                for candidate in candidates:
                    resolved = candidate.resolve()
                    if resolved not in seen:
                        paths.append(candidate)
                        seen.add(resolved)
                        break
        return paths

    MediaRunner._final_paths = staticmethod(final_paths)
    MediaRunner._lumi_output_guard = True


def _install_archive_start_guard() -> None:
    runner = runtime_wave3.Wave3HTTPTransferRunner
    if getattr(runner, "_lumi_archive_start_guard", False):
        return

    def complete_file(self, task, partial: Path, final: Path) -> None:
        runtime_wave3._BaseHTTPTransferRunner._complete_file(
            self,
            task,
            partial,
            final,
        )
        completed = self.store.get_task(task.id)
        if completed is None or not completed.post_process.get("extract"):
            return

        completed.status = TaskStatus.POST_PROCESSING.value
        completed.post_process["archive_source"] = str(final)
        completed.post_process["status"] = "starting"
        self.store.save_task(completed)
        try:
            job = runtime_wave3._controller(self.store).submit_archive(
                completed.id,
                final,
                Path(completed.target_dir),
                delete_archive=bool(
                    completed.post_process.get("delete_archive")
                ),
            )
            completed = self.store.get_task(task.id) or completed
            completed.post_process["status"] = "queued"
            completed.post_process["job_id"] = getattr(job, "id", "")
            self.store.save_task(completed)
        except Exception as exc:
            completed = self.store.get_task(task.id) or completed
            completed.status = TaskStatus.COMPLETED.value
            completed.error = ""
            completed.error_code = ""
            completed.post_process["status"] = "failed_to_start"
            completed.post_process["warning"] = str(exc)
            completed.metadata["completion_warning"] = (
                "Download completed, but archive extraction could not start."
            )
            self.store.save_task(completed)
            self.store.append_event(
                completed.id,
                "archive_postprocess_start_failed",
                {"error": str(exc), "archive_path": str(final)},
            )

    runner._complete_file = complete_file
    runner._lumi_archive_start_guard = True


def install() -> None:
    _install_media_output_guard()
    _install_archive_start_guard()


install()
