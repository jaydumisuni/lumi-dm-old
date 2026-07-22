from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time

from core.v2.models import TaskStatus
from core.v2.runtime import LumiRuntime


PAYLOAD = (b"LUMI-DM-RANGE-PROOF-" * 524288)[: 10 * 1024 * 1024]
ETAG = '"lumi-test-v1"'


class RangeHandler(BaseHTTPRequestHandler):
    ignore_ranges_after_probe = False
    request_count = 0
    slow = False

    def do_GET(self) -> None:
        type(self).request_count += 1
        range_header = self.headers.get("Range", "")

        if (
            self.ignore_ranges_after_probe
            and type(self).request_count > 1
            and range_header
        ):
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.send_header("ETag", ETAG)
            self.end_headers()
            self.wfile.write(PAYLOAD)
            return

        if range_header:
            unit, raw = range_header.split("=", 1)
            assert unit == "bytes"
            start_raw, end_raw = raw.split("-", 1)
            start = int(start_raw)
            end = int(end_raw) if end_raw else len(PAYLOAD) - 1
            end = min(end, len(PAYLOAD) - 1)
            body = PAYLOAD[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Content-Range",
                f"bytes {start}-{end}/{len(PAYLOAD)}",
            )
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", ETAG)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            for offset in range(0, len(body), 64 * 1024):
                self.wfile.write(body[offset : offset + 64 * 1024])
                self.wfile.flush()
                if self.slow:
                    time.sleep(0.01)
            return

        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.send_header("ETag", ETAG)
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *_args) -> None:
        return


class Server:
    def __init__(self, handler=RangeHandler):
        handler.request_count = 0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            daemon=True,
        )

    def __enter__(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.httpd.server_port}/file.bin"

    def __exit__(self, *_args) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=3)
        self.httpd.server_close()


def wait_for(
    runtime: LumiRuntime,
    task_id: str,
    statuses: set[str],
    timeout: float = 15,
):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = runtime.get_task(task_id)
        if task is not None and task.status in statuses:
            return task
        time.sleep(0.05)
    task = runtime.get_task(task_id)
    raise AssertionError(
        f"Task did not reach {statuses}; "
        f"current={task.status if task else None}, "
        f"error={task.error if task else None}"
    )


def test_parallel_http_download_produces_exact_file(tmp_path: Path) -> None:
    RangeHandler.ignore_ranges_after_probe = False
    RangeHandler.slow = False
    with Server() as url:
        runtime = LumiRuntime(tmp_path / "data")
        task = runtime.create_http_task(
            url,
            target_dir=tmp_path / "downloads",
            temp_dir=tmp_path / "temporary",
            filename="proof.bin",
            connections=4,
        )
        completed = wait_for(
            runtime,
            task.id,
            {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value},
        )

        assert completed.status == TaskStatus.COMPLETED.value, completed.error
        assert Path(completed.final_path).read_bytes() == PAYLOAD
        assert runtime.store.load_resume(task.id) is None
        runtime.close()


def test_strict_range_validation_rejects_server_that_ignores_segments(
    tmp_path: Path,
) -> None:
    RangeHandler.ignore_ranges_after_probe = True
    RangeHandler.slow = False
    with Server() as url:
        runtime = LumiRuntime(tmp_path / "data")
        task = runtime.create_http_task(
            url,
            target_dir=tmp_path / "downloads",
            temp_dir=tmp_path / "temporary",
            filename="bad.bin",
            connections=4,
        )
        failed = wait_for(
            runtime,
            task.id,
            {TaskStatus.FAILED.value, TaskStatus.COMPLETED.value},
        )

        assert failed.status == TaskStatus.FAILED.value
        assert failed.error_code == "range_validation"
        assert not Path(failed.final_path).exists()
        runtime.close()


def test_pause_restart_and_resume_uses_segment_journal(tmp_path: Path) -> None:
    RangeHandler.ignore_ranges_after_probe = False
    RangeHandler.slow = True
    with Server() as url:
        data_dir = tmp_path / "data"
        runtime = LumiRuntime(data_dir)
        task = runtime.create_http_task(
            url,
            target_dir=tmp_path / "downloads",
            temp_dir=tmp_path / "temporary",
            filename="resume.bin",
            connections=4,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            current = runtime.get_task(task.id)
            if current and current.downloaded_bytes > 512 * 1024:
                break
            time.sleep(0.03)

        runtime.pause(task.id)
        paused = wait_for(runtime, task.id, {TaskStatus.PAUSED.value})
        journal = runtime.store.load_resume(task.id)

        assert paused.downloaded_bytes > 0
        assert journal is not None
        assert any(item["downloaded"] > 0 for item in journal["segments"])
        runtime.close()

        restarted = LumiRuntime(data_dir)
        restored = restarted.get_task(task.id)
        assert restored is not None
        assert restored.status == TaskStatus.PAUSED.value

        restarted.resume(task.id)
        completed = wait_for(
            restarted,
            task.id,
            {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value},
            timeout=20,
        )

        assert completed.status == TaskStatus.COMPLETED.value, completed.error
        assert Path(completed.final_path).read_bytes() == PAYLOAD
        restarted.close()
