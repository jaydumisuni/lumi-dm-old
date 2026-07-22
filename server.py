"""Lumi Download Manager source launcher."""
from core.v2.server_app import app, main
from core.v3.api import wave3_api

# Browser capture is capped at 4 MiB. Keep enough JSON/base64 overhead for a
# legitimate envelope while rejecting unbounded local API payloads.
app.config.setdefault("MAX_CONTENT_LENGTH", 8 * 1024 * 1024)
if "lumi_wave3" not in app.blueprints:
    app.register_blueprint(wave3_api)

__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
