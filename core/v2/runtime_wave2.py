"""Wave 2 runtime activation.

The stable Wave 1 runtime remains the task/queue authority. This module swaps in
the secure replay-capable HTTP runner before exposing the same public API.
"""
from . import runtime as _runtime
from .http_replay import HTTPTransferRunner

_runtime.HTTPTransferRunner = HTTPTransferRunner

from .runtime import *  # noqa: E402,F401,F403
