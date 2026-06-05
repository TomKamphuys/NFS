"""Diagnostics for NiceGUI timers that outlive their UI parent slot."""

from __future__ import annotations

import traceback
from typing import Any

from loguru import logger
from nicegui.elements.timer import Timer as ElementTimer


_installed = False


def _callback_name(callback: Any) -> str:
    if callback is None:
        return "None"
    module = getattr(callback, "__module__", None)
    qualname = getattr(callback, "__qualname__", None)
    if module and qualname:
        return f"{module}.{qualname}"
    return repr(callback)


def install_timer_identity_logging() -> None:
    """Log useful identity details when a NiceGUI element timer loses its parent."""
    global _installed
    if _installed:
        return
    _installed = True

    original_init = ElementTimer.__init__
    original_get_context = ElementTimer._get_context

    def debug_init(self, interval, callback, *args, **kwargs):  # type: ignore[no-untyped-def]
        original_init(self, interval, callback, *args, **kwargs)
        self._nfs_timer_callback = _callback_name(callback)
        self._nfs_timer_interval = interval
        self._nfs_timer_once = kwargs.get("once", False)
        self._nfs_timer_immediate = kwargs.get("immediate", True)
        self._nfs_timer_creation_stack = "".join(traceback.format_stack(limit=8))

    def debug_get_context(self):  # type: ignore[no-untyped-def]
        try:
            return original_get_context(self)
        except RuntimeError as exc:
            if "parent slot of the element has been deleted" in str(exc).lower():
                if not getattr(self, "_nfs_timer_context_failure_logged", False):
                    self._nfs_timer_context_failure_logged = True
                    logger.error(
                        "NiceGUI timer lost parent slot: callback={!r}, interval={!r}, "
                        "once={!r}, immediate={!r}, active={!r}, deleted={!r}\n{}",
                        getattr(self, "_nfs_timer_callback", "unknown"),
                        getattr(self, "_nfs_timer_interval", "unknown"),
                        getattr(self, "_nfs_timer_once", "unknown"),
                        getattr(self, "_nfs_timer_immediate", "unknown"),
                        getattr(self, "active", "unknown"),
                        getattr(self, "is_deleted", "unknown"),
                        getattr(self, "_nfs_timer_creation_stack", "creation stack unavailable"),
                    )
            raise

    ElementTimer.__init__ = debug_init
    ElementTimer._get_context = debug_get_context
