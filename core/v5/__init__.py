"""Lumi DM finishing services: firmware discovery, branding and desktop handoff."""

# Apply catalogue guarantees before API functions bind provider callables.
from . import firmware_hardening as _firmware_hardening  # noqa: F401
from .api import install_v5

__all__ = ["install_v5"]
