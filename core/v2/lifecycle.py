"""Safe source-runtime shutdown registration for Lumi DM."""
from __future__ import annotations

import atexit
import threading

from .runtime import LumiRuntime


_lock = threading.Lock()
_registered: set[int] = set()
_closed: set[int] = set()


def register_lifecycle(runtime: LumiRuntime) -> None:
    identity = id(runtime)
    with _lock:
        if identity in _registered:
            return
        _registered.add(identity)

    def close_runtime() -> None:
        with _lock:
            if identity in _closed:
                return
            _closed.add(identity)
        try:
            runtime.close()
        except Exception:
            # Interpreter shutdown may already have torn down dependencies.
            pass

    atexit.register(close_runtime)
