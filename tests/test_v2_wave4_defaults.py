from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_blank_source_destinations_use_live_lumi_settings(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = r'''
import json
from pathlib import Path
import server

client = server.app.test_client()
bootstrap = client.post(
    '/api/auth/bootstrap',
    json={'name': 'defaults proof'},
    environ_base={'REMOTE_ADDR': '127.0.0.1'},
)
assert bootstrap.status_code == 200, bootstrap.data
token = bootstrap.get_json()['token']
headers = {'Authorization': f'Bearer {token}'}

final_root = Path(__import__('os').environ['LUMI_FINAL_ROOT'])
temp_root = Path(__import__('os').environ['LUMI_TEMP_ROOT'])
assert client.post(
    '/api/settings/default-dir',
    json={'dir': str(final_root)},
    headers=headers,
).status_code == 200
assert client.post(
    '/api/settings/temp-dir',
    json={'dir': str(temp_root)},
    headers=headers,
).status_code == 200

created = client.post(
    '/api/downloads/start',
    json={
        'url': 'https://example.invalid/live-default.pdf',
        'filename': 'live-default.pdf',
        'category_id': 'documents',
        'start_paused': True,
    },
    headers=headers,
)
assert created.status_code == 200, created.data
body = created.get_json()
assert Path(body['target_dir']).parent == final_root, body
assert Path(body['target_dir']).name == 'Documents', body
assert Path(body['temp_dir']).parent == temp_root, body
assert Path(body['temp_dir']).name == 'Documents', body
print(json.dumps({'ok': True}))
'''
    environment = dict(os.environ)
    environment["LUMIDM_DATA_DIR"] = str(tmp_path / "data")
    environment["LUMI_FINAL_ROOT"] = str(tmp_path / "final-root")
    environment["LUMI_TEMP_ROOT"] = str(tmp_path / "temp-root")
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
