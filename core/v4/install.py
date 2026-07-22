"""Install Lumi V4 services into the source-runtime Flask application."""
from __future__ import annotations

import atexit
from datetime import datetime, timezone
import threading
import time

from flask import Flask

from core.v2 import runtime as runtime_module
from core.v3 import runtime_wave3

from . import security_hardening as _security_hardening  # noqa: F401
from .api import V4Services, configure_services, wave4_api
from .diagnostics import DiagnosticsService
from .maintenance import MaintenanceService
from .security import SecurityManager, install_security


_INSTALL_LOCK = threading.RLock()
_SHUTDOWN = False


def _startup_backup(services: V4Services) -> None:
    key = "maintenance.last_startup_backup.v1"
    previous = float(services.runtime.store.get_setting(key, 0) or 0)
    now = time.time()
    if now - previous < 24 * 60 * 60:
        return
    try:
        services.maintenance.backup_database("startup")
        services.runtime.store.set_setting(key, now)
        services.maintenance.cleanup_backups(keep=20)
    except Exception as exc:
        services.runtime.store.append_event(
            None,
            "startup_backup_failed",
            {"error": str(exc)},
        )


def _safe_shutdown(services: V4Services) -> None:
    global _SHUTDOWN
    with _INSTALL_LOCK:
        if _SHUTDOWN:
            return
        _SHUTDOWN = True
    try:
        services.runtime.store.append_event(
            None,
            "runtime_shutdown",
            {"at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        )
    except Exception:
        pass
    try:
        for controller in list(runtime_wave3._CONTROLLERS.values()):
            controller.close()
        runtime_wave3._CONTROLLERS.clear()
    except Exception:
        pass
    try:
        services.runtime.close()
    except Exception:
        pass


def install_v4(app: Flask) -> V4Services:
    with _INSTALL_LOCK:
        existing = app.extensions.get("lumi_v4")
        if isinstance(existing, V4Services):
            return existing

        runtime = runtime_module._require_runtime()
        security = SecurityManager(runtime.store)
        maintenance = MaintenanceService(runtime.store)
        diagnostics = DiagnosticsService(runtime.store, maintenance)
        services = V4Services(
            runtime=runtime,
            security=security,
            maintenance=maintenance,
            diagnostics=diagnostics,
        )
        configure_services(services)
        if "lumi_wave4" not in app.blueprints:
            app.register_blueprint(wave4_api)
        install_security(app, security)
        app.extensions["lumi_v4"] = services
        _startup_backup(services)
        try:
            maintenance.scan_missing_files(mark=True)
        except Exception:
            pass
        atexit.register(_safe_shutdown, services)
        return services
