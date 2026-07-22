from __future__ import annotations

from pathlib import Path
import threading

from core.v2.models import DownloadTask, RequestEnvelope, TaskStatus, TaskType
from core.v2.store import StateStore
from core.v3.media import MediaInspector, MediaRunner
from core.v3.torrent import TorrentFile, TorrentRunner, TorrentSnapshot


class FakeYDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, url, download=False):
        if not download:
            return {
                "id": "playlist-1",
                "title": "Lumi Playlist",
                "webpage_url": url,
                "entries": [
                    {
                        "id": "one",
                        "title": "First",
                        "url": "https://media.invalid/one",
                        "duration": 10,
                    },
                    {
                        "id": "two",
                        "title": "Second",
                        "url": "https://media.invalid/two",
                        "duration": 20,
                    },
                ],
                "formats": [
                    {
                        "format_id": "1080",
                        "ext": "mp4",
                        "height": 1080,
                        "fps": 60,
                        "vcodec": "avc1",
                        "acodec": "none",
                        "filesize": 1000,
                    },
                    {
                        "format_id": "audio",
                        "ext": "m4a",
                        "vcodec": "none",
                        "acodec": "aac",
                        "abr": 128,
                        "filesize": 200,
                    },
                ],
                "subtitles": {"en": [{"ext": "vtt", "name": "English"}]},
                "automatic_captions": {"bem": [{"ext": "vtt"}]},
                "thumbnails": [{"url": "https://img.invalid/1.jpg", "width": 1280}],
            }

        output_dir = Path(self.options["outtmpl"]).parent
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "Lumi Video [video-1].mp4"
        hooks = self.options.get("progress_hooks") or []
        for hook in hooks:
            hook(
                {
                    "status": "downloading",
                    "filename": str(output) + ".part",
                    "downloaded_bytes": 5,
                    "total_bytes": 10,
                    "speed": 100,
                    "eta": 1,
                    "info_dict": {"id": "video-1", "title": "Lumi Video"},
                }
            )
        output.write_bytes(b"0123456789")
        for hook in hooks:
            hook(
                {
                    "status": "finished",
                    "filename": str(output),
                    "downloaded_bytes": 10,
                    "total_bytes": 10,
                    "info_dict": {"id": "video-1", "title": "Lumi Video"},
                }
            )
        for hook in self.options.get("postprocessor_hooks") or []:
            hook({"status": "started", "postprocessor": "FFmpegMerger"})
            hook({"status": "finished", "postprocessor": "FFmpegMerger"})
        return {
            "id": "video-1",
            "title": "Lumi Video",
            "requested_downloads": [{"filepath": str(output)}],
        }


def _media_task(store: StateStore, tmp_path: Path) -> DownloadTask:
    task = DownloadTask(
        id="media-task",
        type=TaskType.VIDEO.value,
        status=TaskStatus.QUEUED.value,
        request=RequestEnvelope(url="https://media.invalid/watch?v=1"),
        filename="Fetching title…",
        target_dir=str(tmp_path / "media"),
        temp_dir=str(tmp_path / "temp"),
        final_path="",
        partial_path="",
        connections=4,
        metadata={
            "format_id": "1080+audio",
            "subtitles": True,
            "subtitle_languages": ["en", "bem"],
            "thumbnail": True,
            "metadata": True,
        },
    )
    store.save_task(task)
    return task


def test_media_inspector_returns_playlist_formats_subtitles_and_thumbnails() -> None:
    inspector = MediaInspector(ydl_factory=FakeYDL)

    result = inspector.inspect("https://media.invalid/playlist", include_playlist=True)

    assert result["source_type"] == "playlist"
    assert [entry["title"] for entry in result["entries"]] == ["First", "Second"]
    assert result["formats"][0]["format_id"] == "1080"
    assert set(result["subtitles"]) == {"en", "bem"}
    assert result["thumbnails"][0]["width"] == 1280


def test_media_runner_tracks_download_and_postprocessing_to_completion(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "data")
    task = _media_task(store, tmp_path)
    runner = MediaRunner(
        store,
        task.id,
        pause_event=threading.Event(),
        cancel_event=threading.Event(),
        ydl_factory=FakeYDL,
    )

    runner.run()

    completed = store.get_task(task.id)
    assert completed is not None
    assert completed.status == TaskStatus.COMPLETED.value
    assert completed.progress_percent == 100.0
    assert Path(completed.final_path).read_bytes() == b"0123456789"
    assert completed.metadata["postprocessor"] == "FFmpegMerger"
    assert completed.metadata["output_files"] == [completed.final_path]
    store.close()


class FakeTorrentBackend:
    def __init__(self, _task, _data_dir):
        files = [
            TorrentFile(0, "movie.mkv", 100, selected=True, priority=7),
            TorrentFile(1, "sample.mkv", 20, selected=False, priority=0),
        ]
        self.snapshots = [
            TorrentSnapshot(name="Movie", state="resolving_metadata"),
            TorrentSnapshot(
                name="Movie",
                state="downloading",
                metadata_ready=True,
                progress=0.5,
                total_bytes=100,
                downloaded_bytes=50,
                download_rate=500,
                upload_rate=20,
                peers=8,
                seeds=3,
                files=files,
                output_path="/downloads/Movie",
            ),
            TorrentSnapshot(
                name="Movie",
                state="seeding",
                metadata_ready=True,
                progress=1.0,
                total_bytes=100,
                downloaded_bytes=100,
                uploaded_bytes=120,
                ratio=1.2,
                peers=4,
                seeds=4,
                finished=True,
                seeding=True,
                files=files,
                output_path="/downloads/Movie",
            ),
        ]
        self.index = 0
        self.paused = False
        self.cancelled = False
        self.resume_saved = False

    def poll(self):
        current = self.snapshots[min(self.index, len(self.snapshots) - 1)]
        self.index += 1
        return current

    def pause(self):
        self.paused = True

    def cancel(self):
        self.cancelled = True

    def save_resume(self):
        self.resume_saved = True

    def close(self):
        return


def _torrent_task(store: StateStore, tmp_path: Path) -> DownloadTask:
    task = DownloadTask(
        id="torrent-task",
        type=TaskType.TORRENT.value,
        status=TaskStatus.QUEUED.value,
        request=RequestEnvelope(url="magnet:?xt=urn:btih:abc&dn=Movie"),
        filename="Movie",
        target_dir=str(tmp_path / "downloads"),
        temp_dir=str(tmp_path / "temp"),
        final_path=str(tmp_path / "downloads"),
        partial_path="",
        metadata={
            "selected_files": [0],
            "file_priorities": [7, 0],
            "seed_ratio": 1.0,
            "seed_time_seconds": 0,
        },
    )
    store.save_task(task)
    return task


def test_torrent_runner_persists_files_peers_ratio_and_seeding_policy(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "data")
    task = _torrent_task(store, tmp_path)
    runner = TorrentRunner(
        store,
        tmp_path / "data",
        task.id,
        pause_event=threading.Event(),
        cancel_event=threading.Event(),
        backend_factory=FakeTorrentBackend,
        poll_interval=0.01,
    )

    runner.run()

    completed = store.get_task(task.id)
    assert completed is not None
    assert completed.status == TaskStatus.COMPLETED.value
    assert completed.progress_percent == 100.0
    assert completed.metadata["ratio"] == 1.2
    assert completed.metadata["peers"] == 4
    assert completed.metadata["seeds"] == 4
    assert completed.metadata["files"][0]["priority"] == 7
    assert completed.metadata["files"][1]["selected"] is False
    store.close()


def test_torrent_pause_is_a_clean_persistent_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    task = _torrent_task(store, tmp_path)
    pause = threading.Event()
    pause.set()
    runner = TorrentRunner(
        store,
        tmp_path / "data",
        task.id,
        pause_event=pause,
        cancel_event=threading.Event(),
        backend_factory=FakeTorrentBackend,
        poll_interval=0.01,
    )

    runner.run()

    paused = store.get_task(task.id)
    assert paused is not None
    assert paused.status == TaskStatus.PAUSED.value
    store.close()
