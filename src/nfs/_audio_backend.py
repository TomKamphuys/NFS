"""Deferred hardware access; importing this module never loads PortAudio.

First attribute access imports sounddevice and initializes PortAudio on the
calling thread. The Qt application routes discovery, rate probes and stream
operations through BackendManager's persistent COM-initialized audio worker.
This adapter does not dispatch calls: standalone callers must arrange their own
thread/COM lifetime before hardware access.
"""

import os
from importlib import import_module


_OPERATIONS = frozenset({
    "query_hostapis", "query_devices", "check_input_settings",
    "check_output_settings", "AsioSettings", "WasapiSettings", "Stream", "stop",
})


def __getattr__(name: str):
    if name not in _OPERATIONS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # Configure the driver before the first load; Python owns the module cache.
    os.environ["SD_ENABLE_ASIO"] = "1"
    return getattr(import_module("sounddevice"), name)
