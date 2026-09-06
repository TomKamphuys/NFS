# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build specification for the HALS Near Field Scanner GUI.

Build (from the repository root)::

    pyinstaller packaging/hals.spec --noconfirm

This produces a self-contained ``dist/HALS Near Field Scanner`` folder that
does not require Python, uv, or any other developer tooling. The Inno Setup
script in ``packaging/installer.iss`` wraps that folder into a user-friendly
Windows installer.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

# ``__file__`` is not defined while PyInstaller executes a spec file, so the
# project root is derived from the current working directory (the build is
# always launched from the repository root, see the header above).
PROJECT_ROOT = Path.cwd()
SRC_DIR = PROJECT_ROOT / "src"

# --- Data files ------------------------------------------------------------
# Bundle the default configuration next to the executable. The launcher copies
# these into a writable per-user directory on first run.
datas = []
for config_name in ("config.ini", "config_default.ini"):
    config_path = PROJECT_ROOT / config_name
    if config_path.exists():
        datas.append((str(config_path), "."))

# Application images (window icon, splash, etc.).
images_dir = PROJECT_ROOT / "images"
if images_dir.exists():
    datas.append((str(images_dir), "images"))

# Qt button icons shipped inside the harmonic_drive_qt package. They are
# resolved at runtime via ``Path(__file__).parent / "icons"``, so they must
# keep the same relative location inside the bundle.
datas += collect_data_files("harmonic_drive_qt", includes=["icons/*", "**/*.png"])

# The nfs package ships a bundled config.ini as package data.
datas += collect_data_files("nfs")

# --- Plugin discovery ------------------------------------------------------
# Plugins are loaded at runtime through importlib.metadata entry points
# (see nfs/loader.py). PyInstaller cannot follow those dynamic imports, so the
# plugin modules are declared explicitly and the distribution metadata (which
# lists the entry points) is copied into the bundle.
hiddenimports = [
    "nfs.audio",
    "nfs.motion_manager",
    "nfs.plugins.cylindrical_measurement_points",
    "nfs.plugins.spherical_measurement_points",
    "nfs.plugins.spherical_measurement_points_arcs_random",
    "nfs.plugins.file_measurement_points",
]
hiddenimports += collect_submodules("nfs.plugins")

datas += copy_metadata("nfs")

# Heavy scientific/visualisation dependencies with runtime data or many
# dynamically imported submodules.
for package in ("pyvista", "pyvistaqt", "matplotlib", "scipy", "soundfile", "sounddevice"):
    try:
        datas += collect_data_files(package)
        hiddenimports += collect_submodules(package)
    except Exception:
        # A missing optional dependency should not break the whole build.
        pass

icon_path = PROJECT_ROOT / "images" / "icon.ico"

block_cipher = None


a = Analysis(
    [str(PROJECT_ROOT / "packaging" / "hals_launcher.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "nicegui", "pywebview"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HALS Near Field Scanner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="HALS Near Field Scanner",
)
