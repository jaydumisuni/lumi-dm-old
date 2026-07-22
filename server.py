"""Lumi Download Manager source launcher."""
from core.v2.server_app import app, main

# Browser capture is capped at 4 MiB. Keep enough JSON/base64 overhead for a
# legitimate envelope while rejecting unbounded local API payloads.
app.config.setdefault("MAX_CONTENT_LENGTH", 8 * 1024 * 1024)

__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
