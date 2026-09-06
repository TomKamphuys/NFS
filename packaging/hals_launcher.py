"""Frozen-application entry point for the HALS Near Field Scanner GUI.

This launcher is used by PyInstaller to build the standalone Windows
executable that ships inside the Inno Setup installer. End users run the
resulting ``HALS Near Field Scanner.exe`` directly; they do not need Python,
uv, or any development tooling installed.

Responsibilities of this launcher:

* Provide a writable working directory. The application reads and writes
  ``config.ini`` relative to the current working directory. When installed
  under ``C:\\Program Files`` that location is read-only, so we redirect the
  working directory to a per-user data folder.
* Seed that folder, on first run, with the default configuration files that
  were bundled into the executable.
* Hand control over to :func:`harmonic_drive_qt.main.main`.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _bundle_dir() -> Path:
    """Return the directory that contains bundled data files."""
    # PyInstaller unpacks bundled data under sys._MEIPASS at runtime.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent


def _user_data_dir() -> Path:
    """Return a writable per-user directory for HALS runtime files."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "HALS Near Field Scanner"
    return Path.home() / ".hals-near-field-scanner"


def _seed_config_files(data_dir: Path) -> None:
    """Copy bundled default configuration files into ``data_dir`` if missing."""
    bundle = _bundle_dir()
    for name in ("config_default.ini", "config.ini"):
        source = bundle / name
        target = data_dir / name
        if source.exists() and not target.exists():
            try:
                shutil.copyfile(source, target)
            except OSError:
                # Non-fatal: the application will fall back to its own defaults.
                pass


def _prepare_runtime_environment() -> None:
    """Set up a writable working directory for the frozen application."""
    data_dir = _user_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    _seed_config_files(data_dir)
    os.chdir(data_dir)


def main() -> int:
    if getattr(sys, "frozen", False):
        _prepare_runtime_environment()

    from harmonic_drive_qt.main import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())
