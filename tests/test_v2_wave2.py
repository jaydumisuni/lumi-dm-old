from __future__ import annotations

import base64
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from core.v2.categories import CategoryManager, CategoryRule
from core.v2.host_profiles import HostProfile, HostProfileManager
from core.v2.models import RequestEnvelope, TaskStatus
from core.v2.resolvers import default_registry
from core.v2.runtime_wave2 import LumiRuntime
from core.v2.store import StateStore
from core.v2.vault import hydrate_post_body, secure_request_envelope
import core.v2.runtime as runtime_module
import core.v2.wave2 as wave2_module
from core.v2.wave2 import services
from core.v2.wave2_repair import repair_download_link


POST_PAYLOAD = b"Lumi secure POST replay proof"


def _activate_runtime(runtime: LumiRuntime) -> None:
    runtime_module._RUNTIME = runtime
    wave2_module._SERVICES = None


def _wait(runtime: LumiRuntime, task_id: str, timeout: float = 12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = runtime.get_task(task_id)
        if task and task.status in {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.NEEDS_LINK.value,
        }:
            return task
        time.sleep(0.05)
    task = runtime.get_task(task_id)
    raise AssertionError(
        f"task did not finish: {task.status if task else None} "
        f"{task.error if task else None}"
    )


def test_vault_removes_secrets_from_persistable_envelope(tmp_path: Path) -> None:
    secured = secure_request_envelope(
        tmp_path,
        {
            "url": "https://example.invalid/file",
            "method": "POST",
            "headers": {
                "Authorization": "Bearer top-secret",
                "Cookie": "session=private",
                "Referer": "https://example.invalid/page",
            },
            "post_body": {
                "kind": "base64",
                "data": base64.b64encode(b"a=1&b=2").decode("ascii"),
            },
        },
    )

    persisted = json.dumps(secured)
    assert "top-secret" not in persisted
    assert "session=private" not in persisted
    assert secured["headers"] == {"Referer": "https://example.invalid/page"}

    envelope = RequestEnvelope.from_dict(secured)
    replay_headers = envelope.normalized_headers()
    assert replay_headers["Authorization"] == "Bearer top-secret"
    assert replay_headers["Cookie"] == "session=private"
    assert envelope.redacted_dict()["headers"]["Authorization"] == "<redacted>"
    assert hydrate_post_body(envelope.post_body_reference) == b"a=1&b=2"


def test_categories_choose_separate_final_and_temporary_folders(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    manager = CategoryManager(store)

    decision = manager.resolve(
        filename="manual.pdf",
        url="https://example.invalid/manual.pdf",
        base_dir=tmp_path / "downloads",
        temp_base_dir=tmp_path / "temporary",
    )

    assert decision.category_id == "documents"
    assert decision.target_dir == tmp_path / "downloads" / "Documents"
    assert decision.temp_dir == tmp_path / "temporary" / "Documents"
    assert decision.target_dir.is_dir()
    assert decision.temp_dir.is_dir()

    manager.save(
        CategoryRule(
            id="firmware",
            name="Firmware",
            domains=["firmware.example"],
            folder="Firmware",
        )
    )
    custom = manager.resolve(
        filename="payload.bin",
        url="https://cdn.firmware.example/payload.bin",
        base_dir=tmp_path / "downloads",
        temp_base_dir=tmp_path / "temporary",
    )
    assert custom.category_id == "firmware"
    store.close()


def test_host_profile_credentials_are_vaulted_and_applied(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    manager = HostProfileManager(store, tmp_path / "data")
    saved = manager.save(
        HostProfile(
            id="private-host",
            name="Private host",
            host_pattern="files.example",
            max_connections=3,
            speed_limit_bps=2048,
            user_agent="Lumi-Test/2",
            intercept_mode="always_lumi",
        ),
        username="john",
        password="secret",
    )

    public = saved.to_dict(public=True)
    assert public["username_reference"] == "<secure-reference>"
    assert public["password_reference"] == "<secure-reference>"

    envelope, connections, speed, profile = manager.apply(
        RequestEnvelope(url="https://cdn.files.example/archive.zip"),
        connections=8,
        speed_limit_bps=0,
    )
    assert profile is not None
    assert connections == 3
    assert speed == 2048
    assert envelope.headers["User-Agent"] == "Lumi-Test/2"
    assert envelope.headers["Authorization"].startswith("Basic ")

    secured = secure_request_envelope(tmp_path / "data", asdict(envelope))
    assert "Authorization" not in secured["headers"]
    matched = manager.match_url(envelope.url)
    assert matched is not None
    assert matched.intercept_mode == "always_lumi"
    store.close()


def test_resolver_registry_routes_special_sources_before_direct_http() -> None:
    registry = default_registry()

    assert registry.resolver_for(
        RequestEnvelope(url="magnet:?xt=urn:btih:abc")
    ).id == "torrent"
    assert registry.resolver_for(
        RequestEnvelope(url="https://cdn.example/stream/master.m3u8")
    ).id == "hls-dash"
    assert registry.resolver_for(
        RequestEnvelope(url="https://www.youtube.com/watch?v=abc")
    ).id == "yt-dlp"
    assert registry.resolver_for(
        RequestEnvelope(url="https://example.invalid/file.bin")
    ).id == "direct-http"


def test_duplicate_reuse_and_category_are_applied_before_transfer(tmp_path: Path) -> None:
    runtime = LumiRuntime(tmp_path / "data")
    _activate_runtime(runtime)
    runtime.queue.update_queue("default", active=False)

    first = services().start_http(
        "https://example.invalid/manual.pdf",
        target_dir=tmp_path / "downloads",
        temp_dir=tmp_path / "temporary",
        filename="manual.pdf",
        start_paused=True,
        duplicate_policy="reuse",
    )
    second = services().start_http(
        "https://example.invalid/manual.pdf",
        target_dir=tmp_path / "downloads",
        temp_dir=tmp_path / "temporary",
        filename="manual.pdf",
        start_paused=True,
        duplicate_policy="reuse",
    )

    assert first["id"] == second["id"]
    assert first["category_id"] == "documents"
    assert Path(first["target_dir"]).name == "Documents"
    assert Path(first["temp_dir"]).name == "Documents"
    runtime.close()
    runtime_module._RUNTIME = None
    wave2_module._SERVICES = None


class PostHandler(BaseHTTPRequestHandler):
    received: dict[str, object] = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).received = {
            "authorization": self.headers.get("Authorization"),
            "cookie": self.headers.get("Cookie"),
            "body": body,
        }
        if (
            self.headers.get("Authorization") != "Bearer secure-token"
            or self.headers.get("Cookie") != "session=browser-cookie"
            or body != b"a=1&b=2"
        ):
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(POST_PAYLOAD)))
        self.send_header("Content-Disposition", 'attachment; filename="post-proof.bin"')
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        self.wfile.write(POST_PAYLOAD)

    def log_message(self, *_args) -> None:
        return


def test_encrypted_post_request_replays_and_downloads(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), PostHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/generate"

    runtime = LumiRuntime(tmp_path / "data")
    _activate_runtime(runtime)
    try:
        result = services().start_http(
            url,
            target_dir=tmp_path / "downloads",
            temp_dir=tmp_path / "temporary",
            filename="post-proof.bin",
            connections=4,
            request_envelope={
                "url": url,
                "method": "POST",
                "headers": {
                    "Authorization": "Bearer secure-token",
                    "Cookie": "session=browser-cookie",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                "post_body": {
                    "kind": "base64",
                    "data": base64.b64encode(b"a=1&b=2").decode("ascii"),
                },
            },
        )
        completed = _wait(runtime, result["id"])

        assert completed.status == TaskStatus.COMPLETED.value, completed.error
        assert Path(completed.final_path).read_bytes() == POST_PAYLOAD
        assert PostHandler.received["authorization"] == "Bearer secure-token"
        row = runtime.store._conn.execute(
            "SELECT task_json FROM tasks WHERE id=?",
            (completed.id,),
        ).fetchone()[0]
        assert "secure-token" not in row
        assert "browser-cookie" not in row
    finally:
        runtime.close()
        runtime_module._RUNTIME = None
        wave2_module._SERVICES = None
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_repair_wait_and_direct_repair_use_secure_envelope(tmp_path: Path) -> None:
    runtime = LumiRuntime(tmp_path / "data")
    _activate_runtime(runtime)
    runtime.queue.update_queue("default", active=False)
    task = runtime.create_http_task(
        "https://example.invalid/expired.bin",
        target_dir=tmp_path / "downloads",
        temp_dir=tmp_path / "temporary",
        filename="expired.bin",
        start_paused=True,
    )
    waiting = services().set_repair_wait(
        task.id,
        original_page="https://example.invalid/account",
    )
    assert waiting["task_id"] == task.id

    repaired = repair_download_link(
        task.id,
        {
            "url": "https://example.invalid/new-request",
            "method": "POST",
            "headers": {"Authorization": "Bearer replacement"},
            "post_body": {"kind": "text", "data": "ticket=1"},
        },
    )
    stored = runtime.get_task(task.id)

    assert repaired["status"] == TaskStatus.QUEUED.value
    assert repaired["request"]["headers"]["Authorization"] == "<redacted>"
    assert stored is not None
    assert "Authorization" not in stored.request.headers
    assert stored.request.secret_headers_reference
    runtime.close()
    runtime_module._RUNTIME = None
    wave2_module._SERVICES = None


def test_server_launcher_and_browser_contracts_are_present(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["LUMIDM_DATA_DIR"] = str(tmp_path / "server-data")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import server; "
                "routes={rule.rule for rule in server.app.url_map.iter_rules()}; "
                "required={'/api/resolve','/api/categories','/api/host-profiles',"
                "'/api/browser/repair-pending','/api/browser/repair-capture'}; "
                "assert required <= routes, required-routes"
            ),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr

    manifest = json.loads((root / "browser-extension" / "manifest.json").read_text())
    background = (root / "browser-extension" / "background.js").read_text()
    assert "cookies" in manifest["permissions"]
    assert "lumi-force-next" in manifest["commands"]
    assert "isLocalServer" in background
    assert "Request secrets can only be sent to local Lumi" in background
    assert "/api/browser/repair-capture" in background
    assert "Authorization" not in json.dumps(manifest)
