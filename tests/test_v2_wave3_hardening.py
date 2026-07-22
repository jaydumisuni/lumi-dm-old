from __future__ import annotations

from pathlib import Path
import stat
import threading

import pytest

from core.v2.bencode_safe import BencodeError, bdecode
from core.v2.media import MediaService
import core.v2.media as media_module
from core.v2.models import DownloadTask, RequestEnvelope, TaskStatus, TaskType
from core.v2.postprocess import FFmpegPlan, FFmpegService
from core.v2.store import StateStore


def executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def media_task(tmp_path: Path) -> DownloadTask:
    target = tmp_path / "downloads"
    target.mkdir(parents=True, exist_ok=True)
    return DownloadTask(
        id="wrapped-control",
        type=TaskType.VIDEO.value,
        status=TaskStatus.PAUSED.value,
        request=RequestEnvelope(url="https://video.example/item"),
        filename="video.mp4",
        target_dir=str(target),
        temp_dir=str(tmp_path / "temporary"),
        final_path=str(target / "video.mp4"),
        partial_path="",
        metadata={"format_id": "best"},
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"l1:a",             # unterminated list
        b"d1:ai1e",         # unterminated dictionary
        b"4:abc",            # truncated string
        b"i03e",             # leading-zero integer
        b"d1:bi1e1:ai2ee",   # unsorted dictionary keys
        b"d1:ai1e1:ai2ee",   # duplicate dictionary key
        b"x",                # unknown token
    ],
)
def test_bounded_bencode_reports_typed_failures(payload: bytes) -> None:
    with pytest.raises(BencodeError):
        bdecode(payload)


def test_bounded_bencode_rejects_excessive_nesting() -> None:
    payload = b"l" * 70 + b"e" * 70
    with pytest.raises(BencodeError, match="nesting"):
        bdecode(payload, max_depth=32)


class WrappingYoutubeDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, _url, download=False):
        if not download:
            return {"id": "item", "title": "Item", "formats": []}
        try:
            self.options["progress_hooks"][0](
                {
                    "status": "downloading",
                    "downloaded_bytes": 1,
                    "total_bytes": 10,
                }
            )
        except Exception as exc:
            raise RuntimeError("yt-dlp wrapped progress hook failure") from exc
        return None


class WrappingYTDLP:
    YoutubeDL = WrappingYoutubeDL


def test_wrapped_ytdlp_pause_remains_paused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(media_module, "_yt_dlp", WrappingYTDLP)
    store = StateStore(tmp_path / "data")
    task = media_task(tmp_path)
    store.save_task(task)
    pause = threading.Event()
    pause.set()

    result = MediaService(store).run(task, pause, threading.Event())

    assert result.status == TaskStatus.PAUSED.value
    assert result.error == ""
    assert result.error_code == ""
    store.close()


def test_wrapped_ytdlp_cancel_remains_cancelled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(media_module, "_yt_dlp", WrappingYTDLP)
    store = StateStore(tmp_path / "data")
    task = media_task(tmp_path)
    task.id = "wrapped-cancel"
    store.save_task(task)
    cancel = threading.Event()
    cancel.set()

    result = MediaService(store).run(task, threading.Event(), cancel)

    assert result.status == TaskStatus.CANCELLED.value
    assert result.error == ""
    assert result.finished_at
    store.close()


def test_real_ffmpeg_muxer_suffix_is_preserved_on_temporary_output(
    tmp_path: Path,
) -> None:
    ffprobe = executable(
        tmp_path / "ffprobe",
        """#!/usr/bin/env python3
print('{\"format\": {\"duration\": \"1.0\"}}')
""",
    )
    ffmpeg = executable(
        tmp_path / "ffmpeg",
        """#!/usr/bin/env python3
import pathlib, shutil, sys
args = sys.argv[1:]
source = pathlib.Path(args[args.index('-i') + 1])
destination = pathlib.Path(args[-1])
if destination.suffix != '.mp4':
    print('Unable to find a suitable output format')
    raise SystemExit(2)
shutil.copyfile(source, destination)
print('out_time_ms=1000000')
print('progress=end')
""",
    )
    source = tmp_path / "input.mkv"
    source.write_bytes(b"media")
    destination = tmp_path / "output.mp4"

    result = FFmpegService(
        ffmpeg=str(ffmpeg),
        ffprobe=str(ffprobe),
    ).convert(
        source,
        destination,
        FFmpegPlan(output_container="mp4"),
        cancel_event=threading.Event(),
    )

    assert result["status"] == "completed"
    assert destination.read_bytes() == b"media"
    assert not list(tmp_path.glob("*.lumi-processing*"))
