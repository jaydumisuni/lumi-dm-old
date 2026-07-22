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
    source = (root / "browser-extension" / "background.js").read_text(
        encoding="utf-8"
    )

    assert "MAX_CAPTURE_BODY_BYTES = 4 * 1024 * 1024" in source
    assert "POST body exceeds Lumi's 4 MB capture limit" in source
    assert "Browser kept download" in source
    assert "if (envelope.capture_error)" in source
