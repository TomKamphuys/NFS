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
    collect_all,
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

# Native binaries (C-extensions, .pyd/.dll files). PyVista and VTK load a lot
# of their functionality from dynamically imported compiled extensions that
# PyInstaller cannot discover on its own, hence they are collected explicitly
# below via ``collect_all``.
binaries = []

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
# dynamically imported submodules. ``collect_all`` also gathers the compiled
# C-extensions (binaries) that PyVista/VTK load dynamically at runtime, which
# ``collect_data_files``/``collect_submodules`` alone would miss.
for package in (
    "pyvista",
    "pyvistaqt",
    "vtk",
    "vtkmodules",
    "matplotlib",
    "scipy",
    "soundfile",
    "sounddevice",
    # ``soundfile`` and ``sounddevice`` are single-file modules, so
    # ``collect_all`` cannot see them as packages and skips their native
    # libraries with a warning. The actual C libraries (libsndfile, portaudio)
    # live in the companion *data* packages below, which must be collected so
    # audio playback/recording keeps working inside the frozen application.
    "_soundfile_data",
    "_sounddevice_data",
):
    try:
        pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hiddenimports
    except Exception:
        # A missing optional dependency should not break the whole build.
        pass

# ``charset_normalizer`` (pulled in transitively via ``requests``) ships an
# optional mypyc-compiled speed-up. Its shared runtime lives in a top-level,
# hash-named extension module (e.g. ``4c842c94...__mypyc.cp313-win_amd64.pyd``)
# that is *not* part of any package, so PyInstaller does not discover it and the
# application crashes at runtime with ``No module named '...._mypyc'``. Collect
# the package normally and additionally bundle every top-level ``*__mypyc*``
# extension found next to it.
try:
    cn_datas, cn_binaries, cn_hiddenimports = collect_all("charset_normalizer")
    datas += cn_datas
    binaries += cn_binaries
    hiddenimports += cn_hiddenimports
except Exception:
    pass

try:
    import charset_normalizer as _cn

    site_packages_dir = Path(_cn.__file__).resolve().parent.parent
    for mypyc_pyd in site_packages_dir.glob("*__mypyc*.pyd"):
        # Keep the file at the top level of the bundle so the compiled modules
        # can import their shared runtime by its bare module name.
        binaries.append((str(mypyc_pyd), "."))
        module_name = mypyc_pyd.name.split(".")[0]
        if module_name not in hiddenimports:
            hiddenimports.append(module_name)
except Exception:
    pass

# Ensure the top-level dynamic import names are always present even if the
# ``collect_all`` calls above skipped something.
for _name in ("pyvista", "pyvistaqt", "vtk", "vtkmodules"):
    if _name not in hiddenimports:
        hiddenimports.append(_name)

icon_path = PROJECT_ROOT / "images" / "icon.ico"

block_cipher = None


a = Analysis(
    [str(PROJECT_ROOT / "packaging" / "hals_launcher.py")],
    pathex=[str(SRC_DIR)],
    binaries=binaries,
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
