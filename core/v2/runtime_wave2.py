"""Wave 2 runtime activation.

The stable Wave 1 runtime remains the task/queue authority. Wave 2 swaps in the
secure replay runner and replay-aware GET probe before exposing the public API.
"""
from . import runtime as _runtime
from .http_replay import HTTPTransferRunner, probe_resource

_runtime.HTTPTransferRunner = HTTPTransferRunner
_runtime.probe_resource = probe_resource

from .runtime import *  # noqa: E402,F401,F403
