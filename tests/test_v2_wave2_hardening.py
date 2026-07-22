from __future__ import annotations

from pathlib import Path

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

    # Simulate damaged or externally removed encrypted state. Replay must fail later,
    # but task listing and diagnostics must remain available and secret-safe.
    entries = tmp_path / "vault" / "entries.json"
    entries.write_text("{}", encoding="utf-8")

    public = envelope.redacted_dict()

    assert public["headers"]["Referer"] == "https://example.invalid/account"
    assert public["headers"]["Sensitive-Headers"] == "<redacted-unavailable>"
    assert public["secret_headers_reference"] == "<secure-reference>"
    assert "private-token" not in str(public)
