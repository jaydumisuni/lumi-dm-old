from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from core.v2.models import RequestEnvelope
from core.v2.vault import secure_request_envelope


def test_public_request_view_survives_missing_vault_entry(tmp_path: Path) -> None:
    secured = secure_request_envelope(
        tmp_path,
        {
            "url": "https://example.invalid/private.bin",
            "headers": {
                "Authorization": "Bearer private-token",
                "Referer": "https://example.invalid/account",
            },
        },
    )
    envelope = RequestEnvelope.from_dict(secured)

    # Replay must fail later if encrypted state is damaged, but listing and
    # diagnostics must remain available and secret-safe.
    entries = tmp_path / "vault" / "entries.json"
    entries.write_text("{}", encoding="utf-8")

    public = envelope.redacted_dict()

    assert public["headers"]["Referer"] == "https://example.invalid/account"
    assert public["headers"]["Sensitive-Headers"] == "<redacted-unavailable>"
    assert public["secret_headers_reference"] == "<secure-reference>"
    assert "private-token" not in str(public)


def test_browser_capture_is_bounded_and_keeps_oversized_posts_in_browser() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "browser-extension" / "browser-bridge.js").read_text(
        encoding="utf-8"
    )

    assert "MAX_BODY=4*1024*1024" in source
    assert "POST body exceeds Lumi's 4 MB capture limit" in source
    assert "Browser kept download" in source
    assert "env.capture_error" in source
    assert "localServer()" in source


def test_local_server_rejects_unbounded_request_envelopes(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["LUMIDM_DATA_DIR"] = str(tmp_path / "server-data")
    proof = (
        "import server; "
        "assert server.app.config['MAX_CONTENT_LENGTH'] == 8 * 1024 * 1024; "
        "client=server.app.test_client(); "
        "response=client.post('/api/security/pair', "
        "data=b'x'*(8*1024*1024+1), content_type='application/json'); "
        "assert response.status_code == 413, response.status_code"
    )
    result = subprocess.run(
        [sys.executable, "-c", proof],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
