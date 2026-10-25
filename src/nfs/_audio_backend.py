"""Deferred hardware backend access; importing this module never loads PortAudio."""

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
