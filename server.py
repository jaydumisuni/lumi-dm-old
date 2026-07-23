"""Lumi Download Manager source launcher."""
from core.v2.server_app import app, main
from core.v3.api import wave3_api
from core.v3 import hardening as _wave3_hardening  # noqa: F401
from core.v4 import install_v4
from core.v5 import install_v5
from core.v5.browser_api import wave5_browser_api
from core.v5.desktop_api import wave5_desktop_api
from core.v5.os_api import install_os_api, wave5_os_api
from core.v6 import install_reliability

# Browser capture is capped at 4 MiB. Keep enough JSON/base64 overhead for a
# legitimate envelope while rejecting unbounded local API payloads.
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
if "lumi_wave3" not in app.blueprints:
    app.register_blueprint(wave3_api)
install_v4(app)
install_v5(app)
install_os_api()
install_reliability()
if "lumi_wave5_browser" not in app.blueprints:
    app.register_blueprint(wave5_browser_api)
if "lumi_wave5_desktop" not in app.blueprints:
    app.register_blueprint(wave5_desktop_api)
if "lumi_wave5_os" not in app.blueprints:
    app.register_blueprint(wave5_os_api)

__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
