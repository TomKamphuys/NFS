"""Keep frozen-app dependency caches and temporary files in app-owned storage.

Run before PyInstaller's library hooks, which can create temporary caches before
the launcher runs. Source checkouts retain their normal environment.
"""

import os
from pathlib import Path
import sys
import tempfile


def configure_runtime_paths() -> None:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    data_dir = Path(base) / "HALS Near Field Scanner" if base else Path.home() / ".hals-near-field-scanner"
    paths = {
        "MPLCONFIGDIR": data_dir / "cache" / "matplotlib",
        "PYVISTA_USERDATA_PATH": data_dir / "cache" / "pyvista",
        "TEMP": data_dir / "temp",
        "TMP": data_dir / "temp",
    }
    for variable, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(path)
    # tempfile may already have cached the system temp path during bootstrapping.
    tempfile.tempdir = str(paths["TEMP"])


if getattr(sys, "frozen", False):
    configure_runtime_paths()
