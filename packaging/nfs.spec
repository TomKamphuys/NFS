# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Near-Field Scanner (HALS) desktop application.

Build locally with:
    pyinstaller packaging/nfs.spec --noconfirm

The build is also driven by the GitHub Actions workflow in
.github/workflows/build.yml so contributors can download a ready-to-run
zip from the Actions/Releases page.

The resulting one-folder distribution is written to ``dist/nfs/`` and
contains the executable plus every runtime asset the GUI needs:
  * ``config.ini``                    - default runtime configuration
  * ``images/``                       - app icon / static UI images
  * bundled NiceGUI / Plotly / pywebview resources
  * the ``grid_generator/images_grid_gen`` diagrams
  * every ``nfs.plugins.*`` module so dynamic plugin discovery works
"""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# ``SPECPATH`` is provided by PyInstaller and points at the directory of this
# spec file.  We resolve the project root one level up so the spec is fully
# location-independent (works locally and in CI).
PROJECT_ROOT = Path(SPECPATH).resolve().parent
SRC = PROJECT_ROOT / "src"

# ---------------------------------------------------------------------------
# Third-party data / hidden imports
# ---------------------------------------------------------------------------
# NiceGUI, Plotly and pywebview ship JavaScript / HTML / image assets that
# PyInstaller does not pick up from import analysis alone.  ``collect_all``
# returns (datas, binaries, hiddenimports) tuples for each package.
datas = []
binaries = []
hiddenimports = []

# ``nicegui``, ``plotly`` and ``webview`` are real packages – use collect_all.
# ``sounddevice`` and ``soundfile`` are single-file modules that ship their
# PortAudio / libsndfile DLLs as package data on the import path, so we need
# collect_dynamic_libs (and a hidden import) rather than collect_all (which
# warns and yields nothing for non-packages).
for pkg in ("nicegui", "plotly", "webview"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    except Exception:
        # Optional packages – skip silently if not installed in the build env.
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

for mod in ("sounddevice", "soundfile"):
    try:
        binaries += collect_dynamic_libs(mod)
    except Exception:
        pass
    hiddenimports.append(mod)

# ``plotly.matplotlylib`` requires matplotlib (which we don't ship) and
# ``webview.platforms.android`` only loads on Android – strip both to silence
# noisy import-time warnings during analysis.
hiddenimports = [
    h for h in hiddenimports
    if not h.startswith("plotly.matplotlylib")
    and not h.startswith("webview.platforms.android")
]

# Loguru sometimes needs its handlers picked up explicitly.
hiddenimports += collect_submodules("loguru")

# Every plugin module is discovered dynamically at runtime via the
# configuration file, so PyInstaller cannot infer them from static imports.
hiddenimports += collect_submodules("nfs.plugins")

# ---------------------------------------------------------------------------
# Project data files (config + images + plugin assets)
# ---------------------------------------------------------------------------
# Bundle the default configuration next to the executable so first-time users
# can just double-click the binary.
datas += [(str(PROJECT_ROOT / "config.ini"), ".")]

# Static UI images (icon, etc.)
images_dir = PROJECT_ROOT / "images"
if images_dir.exists():
    datas += [(str(images_dir), "images")]

# Grid-generator diagrams shipped with the ``grid_generator`` package.
grid_images = SRC / "grid_generator" / "images_grid_gen"
if grid_images.exists():
    datas += [(str(grid_images), "grid_generator/images_grid_gen")]

# Also collect package_data declared in pyproject.toml (defensive: works
# whether or not the project is installed into the build environment).
datas += collect_data_files("nfs", includes=["*.ini"])
datas += collect_data_files("grid_generator", includes=["images_grid_gen/*"])

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(SRC / "harmonic_drive" / "gui.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tests",
        "pytest",
        "sphinx",
        "matplotlib",
        "plotly.matplotlylib",
        "webview.platforms.android",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# Windows / PyInstaller only accept .ico (or .exe) as icon source. The .ico
# file is generated from images/icon.png and checked into the repo so the
# build doesn't depend on Pillow being installed.
icon_path = images_dir / "icon.ico"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="nfs",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # GUI app -> no console window on Windows
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
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="nfs",
)
