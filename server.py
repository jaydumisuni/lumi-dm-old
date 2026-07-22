"""Lumi Download Manager source launcher."""
from core.v2.lifecycle import register_lifecycle
from core.v2.runtime import _require_runtime
from core.v2.security import auth_blueprint, configure_security
from core.v2.server_app import app, main
from core.v2.wave3_api import wave3_blueprint
from core.v2.wave4_api import wave4_blueprint

# Browser capture is capped at 4 MiB. Keep enough JSON/base64 overhead for a
# legitimate envelope while rejecting unbounded local API payloads.
app.config.setdefault("MAX_CONTENT_LENGTH", 8 * 1024 * 1024)

_runtime = _require_runtime()
if "lumi_auth" not in app.blueprints:
    app.register_blueprint(auth_blueprint)
if "lumi_wave3" not in app.blueprints:
    app.register_blueprint(wave3_blueprint)
if "lumi_wave4" not in app.blueprints:
    app.register_blueprint(wave4_blueprint)
if "lumi_auth" not in app.extensions:
    configure_security(app, _runtime.store)
register_lifecycle(_runtime)

__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
