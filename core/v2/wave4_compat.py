"""Wave 4 compatibility interception for legacy direct-start clients."""
from __future__ import annotations

from flask import Flask, request

from .wave4_api import api_product_start_download


def install_wave4_compatibility(app: Flask) -> None:
    if app.extensions.get("lumi_wave4_compat"):
        return
    app.extensions["lumi_wave4_compat"] = True

    @app.before_request
    def route_complete_direct_start():
        # Security is registered before this hook. Authenticated older clients and
        # the browser extension retain their original endpoint while receiving the
        # complete Wave 4 planning contract.
        if request.method == "POST" and request.path == "/api/downloads/start":
            return api_product_start_download()
        return None
