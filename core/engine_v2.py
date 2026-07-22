"""Public Lumi DM v2 engine facade.

Flask imports from this module so the application runtime remains packaging-neutral.
The launch guard closes queue handoff races before any runtime instance is created.
"""
from .v2.runtime import *  # noqa: F401,F403
from .v2 import runtime_guard as _runtime_guard  # noqa: F401
