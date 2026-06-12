"""Small local SVG icon helper for Qt buttons."""

from __future__ import annotations

from pathlib import Path

from .qt_compat import QIcon


ICON_DIR = Path(__file__).resolve().parent / "icons"


def ui_icon(name: str) -> QIcon:
    return QIcon(str(ICON_DIR / f"{name}.svg"))
