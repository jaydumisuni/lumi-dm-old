from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time

import pytest
import requests


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as current:
        current.bind(("127.0.0.1", 0))
        return int(current.getsockname()[1])


def _wait_for_server(session: requests.Session, base_url: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = session.get(base_url, timeout=1)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise AssertionError("Lumi source server did not start")


def test_fresh_source_server_runs_secure_product_workflow(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "lumi-data"
    downloads = tmp_path / "downloads"
    temporary = tmp_path / "temporary"
    moved = tmp_path / "moved"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = tmp_path / "server.log"
    environment = dict(os.environ)
    environment.update(
        {
            "LUMIDM_DATA_DIR": str(data_dir),
            "LUMIDM_DOWNLOAD_DIR": str(downloads),
            "LUMIDM_TEMP_DIR": str(temporary),
            "PYTHONUNBUFFERED": "1",
        }
    )
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "server.py",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=root,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    session = requests.Session()
    try:
        _wait_for_server(session, base_url)
        bootstrap = session.get(
            f"{base_url}/api/security/bootstrap",
            timeout=5,
        )
        assert bootstrap.status_code == 200, bootstrap.text
        assert bootstrap.json()["authenticated"] is True

        overview = session.get(f"{base_url}/api/v4/overview", timeout=5)
        assert overview.status_code == 200, overview.text
        origin = {"Origin": base_url}
        created = session.post(
            f"{base_url}/api/downloads/start",
            json={
                "url": "https://example.invalid/source-proof.bin",
                "filename": "source-proof.bin",
                "target_dir": str(downloads),
                "temp_dir": str(temporary),
                "start_paused": True,
                "duplicate_policy": "reuse",
            },
            headers=origin,
            timeout=8,
        )
        assert created.status_code == 200, created.text
        task = created.json()
        task_id = task["id"]
        recorded = Path(task["path"])
        recorded.parent.mkdir(parents=True, exist_ok=True)
        recorded.write_bytes(b"fresh source runtime proof")

        moved_response = session.post(
            f"{base_url}/api/v4/tasks/{task_id}/move",
            json={
                "target_dir": str(moved),
                "filename": "moved-proof.bin",
            },
            headers=origin,
            timeout=8,
        )
        assert moved_response.status_code == 200, moved_response.text
        moved_path = Path(moved_response.json()["path"])
        assert moved_path.read_bytes() == b"fresh source runtime proof"

        inspector = session.get(
            f"{base_url}/api/v4/tasks/{task_id}/inspector",
            timeout=5,
        )
        assert inspector.status_code == 200, inspector.text
        assert inspector.json()["task"]["filename"] == "moved-proof.bin"

        diagnostic = session.post(
            f"{base_url}/api/v4/diagnostics/export",
            json={},
            headers=origin,
            timeout=12,
        )
        assert diagnostic.status_code == 200, diagnostic.text
        assert Path(diagnostic.json()["path"]).is_file()

        backup = session.post(
            f"{base_url}/api/v4/maintenance/database/backup",
            json={"label": "fresh-checkout"},
            headers=origin,
            timeout=12,
        )
        assert backup.status_code == 200, backup.text
        assert Path(backup.json()["path"]).is_file()
    finally:
        if process.poll() is None:
            if os.name == "posix":
                process.send_signal(signal.SIGINT)
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log.close()

    assert process.returncode in {0, -signal.SIGINT, 130}, log_path.read_text(
        encoding="utf-8"
    )
    database = data_dir / "lumi.db"
    assert database.is_file()
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT event_type FROM events ORDER BY seq DESC LIMIT 30"
        ).fetchall()
    assert "runtime_shutdown" in {row[0] for row in rows}


def test_final_ui_and_extension_sources_are_valid_and_clean() -> None:
    root = Path(__file__).resolve().parents[1]
    index = (root / "static" / "index.html").read_text(encoding="utf-8")
    application = (root / "static" / "app.js").read_text(encoding="utf-8")
    popup = (root / "browser-extension" / "popup.html").read_text(encoding="utf-8")
    manifest = json.loads(
        (root / "browser-extension" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    combined = f"{index}\n{application}\n{popup}"
    assert "Reminal" not in combined
    assert "Rumi" not in combined
    for required in (
        "Overview", "All downloads", "Queues", "Categories", "LinkGrabber",
        "Diagnostics", "Post-processing", "Repair Download Link",
    ):
        assert required in combined
    assert "onclick=" not in popup
    assert manifest["background"]["service_worker"] == "background.js"
    assert manifest["action"]["default_popup"] == "popup.html"
    assert manifest["content_scripts"][0]["js"] == ["content.js", "content-safety.js"]

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed")
    classic_scripts = [
        "static/app.js",
        "static/app-hardening.js",
        "static/technician-workspaces.js",
        "static/operating-systems.js",
        "static/ttg-shell.js",
        "static/ttg-theme.js",
        "browser-extension/security-shim.js",
        "browser-extension/notification-guard.js",
        "browser-extension/content.js",
        "browser-extension/content-safety.js",
        "browser-extension/popup.js",
        "browser-extension/popup-security.js",
        "browser-extension/popup-native-handoff.js",
    ]
    for relative in classic_scripts:
        result = subprocess.run(
            [node, "--check", relative],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, f"{relative}: {result.stderr}"
    module_check = subprocess.run(
        [
            node,
            "--experimental-default-type=module",
            "--check",
            "browser-extension/background.js",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert module_check.returncode == 0, module_check.stderr
