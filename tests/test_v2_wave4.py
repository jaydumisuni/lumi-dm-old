from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from core.v2.inspector import TaskInspector
from core.v2.maintenance import MaintenanceService
from core.v2.models import DownloadTask, RequestEnvelope, TaskStatus, TaskType
from core.v2.runtime_wave3 import LumiRuntime
from core.v2.security import AuthManager
from core.v2.store import StateStore
from core.v2.vault import secure_request_envelope


def make_task(
    tmp_path: Path,
    task_id: str,
    *,
    status: str,
    final_exists: bool = False,
    total_bytes: int = 5,
) -> DownloadTask:
    target = tmp_path / "downloads"
    temporary = tmp_path / "temporary"
    target.mkdir(parents=True, exist_ok=True)
    temporary.mkdir(parents=True, exist_ok=True)
    final = target / f"{task_id}.bin"
    if final_exists:
        final.write_bytes(b"lumi!"[:total_bytes])
    return DownloadTask(
        id=task_id,
        type=TaskType.HTTP.value,
        status=status,
        request=RequestEnvelope(url=f"https://example.invalid/{task_id}.bin"),
        filename=final.name,
        target_dir=str(target),
        temp_dir=str(temporary),
        final_path=str(final),
        partial_path=str(temporary / f"{final.name}.part"),
        total_bytes=total_bytes,
        downloaded_bytes=total_bytes if final_exists else 0,
        progress_percent=100.0 if final_exists else 0.0,
    )


def test_auth_pairing_is_scoped_and_one_time(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    manager = AuthManager(store)

    owner_token, owner = manager.bootstrap_local(
        "127.0.0.1",
        name="Local owner",
    )
    assert owner.scope == "owner"
    assert manager.validate(owner_token).id == owner.id

    pairing = manager.create_pairing_code(name="Phone", scope="read")
    phone_token, phone = manager.exchange_pairing_code(pairing["code"])

    assert phone.scope == "read"
    assert manager.validate(phone_token).id == phone.id
    try:
        manager.exchange_pairing_code(pairing["code"])
    except PermissionError as exc:
        assert "invalid or expired" in str(exc)
    else:
        raise AssertionError("Pairing code was accepted twice")

    assert manager.revoke(phone.id) is True
    assert manager.validate(phone_token) is None
    store.close()


def test_remote_bootstrap_is_denied(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    manager = AuthManager(store)
    try:
        manager.bootstrap_local("192.168.1.20", name="Remote")
    except PermissionError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("Remote client received an owner token")
    store.close()


def test_task_inspector_moves_locates_and_restarts_safely(tmp_path: Path) -> None:
    runtime = LumiRuntime(tmp_path / "data")
    runtime.queue.update_queue("default", active=False)
    task = make_task(
        tmp_path,
        "inspect",
        status=TaskStatus.COMPLETED.value,
        final_exists=True,
    )
    runtime.store.save_task(task)
    inspector = TaskInspector(runtime)

    details = inspector.details(task.id)
    assert details["overview"]["status"] == TaskStatus.COMPLETED.value
    assert "move" in details["actions"]
    assert details["request"]["url"].endswith("inspect.bin")

    moved = inspector.move_or_rename(
        task.id,
        str(tmp_path / "library" / "renamed.bin"),
    )
    assert Path(moved["path"]).read_bytes() == b"lumi!"

    Path(moved["path"]).unlink()
    missing = runtime.get_task(task.id)
    assert missing is not None
    missing.status = TaskStatus.FAILED.value
    missing.error_code = "missing_file"
    runtime.store.save_task(missing)
    replacement = tmp_path / "found.bin"
    replacement.write_bytes(b"lumi!")
    located = inspector.locate(task.id, str(replacement))
    assert located["status"] == TaskStatus.COMPLETED.value
    assert located["path"] == str(replacement)

    partial = Path(located["partial_path"])
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"partial")
    runtime.store.save_resume(
        task.id,
        {"schema_version": 2, "task_id": task.id, "segments": []},
    )
    restarted = inspector.restart(task.id)
    assert restarted["status"] == TaskStatus.QUEUED.value
    assert not partial.exists()
    assert runtime.store.load_resume(task.id) is None
    runtime.close()


def test_backup_verification_and_diagnostics_redact_secrets(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    secured = secure_request_envelope(
        store.data_dir,
        {
            "url": "https://example.invalid/private.bin",
            "headers": {
                "Authorization": "Bearer do-not-export",
                "Cookie": "session=do-not-export",
            },
        },
    )
    task = make_task(
        tmp_path,
        "private",
        status=TaskStatus.PAUSED.value,
    )
    task.request = RequestEnvelope.from_dict(secured)
    store.save_task(task)
    maintenance = MaintenanceService(store)

    backup = maintenance.create_backup(label="proof")
    verified = maintenance.verify_backup(backup["filename"])
    diagnostics = maintenance.export_diagnostics()

    assert verified["ok"] is True
    with zipfile.ZipFile(diagnostics["path"]) as archive:
        tasks = archive.read("tasks.json").decode("utf-8")
        health = archive.read("health.json").decode("utf-8")
    assert "do-not-export" not in tasks
    assert "do-not-export" not in health
    assert "<redacted>" in tasks
    store.close()


def test_repair_creates_backup_and_marks_missing_completed_files(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data")
    task = make_task(
        tmp_path,
        "missing",
        status=TaskStatus.COMPLETED.value,
        final_exists=False,
    )
    store.save_task(task)
    maintenance = MaintenanceService(store)

    result = maintenance.repair()
    repaired = store.get_task(task.id)

    assert result["backup"]["status"] == "created"
    assert Path(result["backup"]["path"]).is_file()
    assert repaired is not None
    assert repaired.status == TaskStatus.FAILED.value
    assert repaired.error_code == "missing_file"
    store.close()


def test_authenticated_server_contract_and_complete_direct_planning(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    script = r'''
import json
import os
from pathlib import Path
import server

client = server.app.test_client()

unauthorized = client.get('/api/downloads')
assert unauthorized.status_code == 401, unauthorized.data

evil = client.post(
    '/api/auth/bootstrap',
    json={'name': 'evil'},
    headers={'Origin': 'https://evil.example'},
    environ_base={'REMOTE_ADDR': '127.0.0.1'},
)
assert evil.status_code == 403, evil.data

bootstrap = client.post(
    '/api/auth/bootstrap',
    json={'name': 'test owner'},
    environ_base={'REMOTE_ADDR': '127.0.0.1'},
)
assert bootstrap.status_code == 200, bootstrap.data
token = bootstrap.get_json()['token']
headers = {'Authorization': f'Bearer {token}'}

pairing = client.post(
    '/api/auth/pair-code',
    json={'name': 'read phone', 'scope': 'read'},
    headers=headers,
)
assert pairing.status_code == 200, pairing.data
code = pairing.get_json()['code']
paired = client.post('/api/auth/pair', json={'code': code})
read_token = paired.get_json()['token']
read_headers = {'Authorization': f'Bearer {read_token}'}
assert client.get('/api/downloads', headers=read_headers).status_code == 200
assert client.post('/api/downloads/pause-all', json={}, headers=read_headers).status_code == 403

target = Path(os.environ['LUMI_TEST_TARGET'])
temporary = Path(os.environ['LUMI_TEST_TEMP'])
started = client.post(
    '/api/downloads/start',
    json={
        'url': 'https://example.invalid/manual.pdf',
        'filename': 'manual.pdf',
        'target_dir': str(target),
        'temp_dir': str(temporary),
        'category_id': 'documents',
        'queue_id': 'default',
        'duplicate_policy': 'reuse',
        'start_paused': True,
    },
    headers=headers,
)
assert started.status_code == 200, started.data
body = started.get_json()
assert body['status'] == 'paused', body
assert body['category_id'] == 'documents', body
assert Path(body['target_dir']).name == 'Documents', body
assert Path(body['temp_dir']).name == 'Documents', body

routes = {rule.rule for rule in server.app.url_map.iter_rules()}
required = {
    '/api/auth/bootstrap', '/api/auth/pair-code', '/api/maintenance/health',
    '/api/downloads/<task_id>/inspect', '/api/product/downloads/start',
    '/api/media/start', '/api/torrents/start',
}
assert required <= routes, required - routes
print(json.dumps({'ok': True, 'task': body['id']}))
'''
    environment = dict(os.environ)
    environment["LUMIDM_DATA_DIR"] = str(tmp_path / "server-data")
    environment["LUMI_TEST_TARGET"] = str(tmp_path / "downloads")
    environment["LUMI_TEST_TEMP"] = str(tmp_path / "temporary")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout.strip().splitlines()[-1])["ok"] is True


def test_final_product_source_contract_has_no_dead_or_legacy_branding() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
    extension_manifest = json.loads(
        (root / "browser-extension" / "manifest.json").read_text(encoding="utf-8")
    )
    entry = (root / "browser-extension" / "background-entry.js").read_text(
        encoding="utf-8"
    )
    auth = (root / "browser-extension" / "auth-bootstrap.js").read_text(
        encoding="utf-8"
    )

    required_ids = {
        "downloads-list", "direct-form", "media-form", "torrent-form",
        "queues-list", "categories-list", "clients-list", "diagnostics-output",
        "inspector", "settings-form",
    }
    for element_id in required_ids:
        assert f'id="{element_id}"' in html
    assert "onclick=" not in html.lower()
    assert "Reminal" not in html + app_js
    assert "Rumi" not in html + app_js
    assert "/api/media/start" in app_js
    assert "/api/torrents/start" in app_js
    assert "/api/maintenance/repair" in app_js
    assert extension_manifest["background"]["service_worker"] == "background-entry.js"
    assert 'import "./auth-bootstrap.js"' in entry
    assert "Authorization" in auth
    assert "isLoopbackApi" in auth
