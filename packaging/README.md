# Packaging the HALS Near Field Scanner for end users

This folder contains everything needed to turn the project into a
double-click Windows installer for people who are **not** developers. The
resulting installer bundles Python and all dependencies, so users never have
to install Python, `uv`, or manage virtual environments.

## What's here

| File | Purpose |
| --- | --- |
| `hals_launcher.py` | Entry point used by the frozen build. Sets up a writable per-user data directory and launches the Qt GUI. |
| `hals.spec` | PyInstaller specification that freezes the app into a self-contained folder. |
| `installer.iss` | Inno Setup script that wraps the frozen folder into a friendly installer. |

## Automated builds (recommended)

The GitHub Actions workflow `.github/workflows/windows-installer.yml` builds
the installer automatically:

* **Manually:** open the *Actions* tab, select *Windows Installer*, and click
  *Run workflow*. Download the resulting `HALS-Near-Field-Scanner-Setup`
  artifact.
* **On release:** push a version tag (matching the `version` in
  `pyproject.toml`), e.g.

  ```bash
  git tag v0.2.1
  git push origin v0.2.1
  ```

  The installer is then attached to the GitHub Release automatically.

## Building locally (optional)

You need Windows, [`uv`](https://docs.astral.sh/uv/), and
[Inno Setup](https://jrsoftware.org/isdl.php) installed.

```powershell
# From the repository root
uv sync --all-extras --dev
uv pip install pyinstaller

# 1. Freeze the application
uv run pyinstaller packaging/hals.spec --noconfirm --clean

# 2. Build the installer (version is optional)
./packaging/verify_frozen_startup.ps1
iscc /DMyAppVersion=0.2.1 packaging\installer.iss
```

The finished installer is written to `dist/installer/`.

## Runtime notes

* The frozen application stores its writable `config.ini` in
  `%LOCALAPPDATA%\HALS Near Field Scanner`. The bundled defaults are copied
  there on first run, so the read-only installation folder is never modified.
* Plugins are discovered through the packaged distribution metadata; the spec
  file copies that metadata and lists the plugin modules as hidden imports.
* Frozen dependency caches and temporary files are contained beneath the app's
  per-user data directory. Uninstall deletes that directory, the roaming/home
  fallback directories, and the legacy `HALS_working_project` temp directory.
  Saved measurement projects and recordings are preserved, as are shared
  library settings and other HALS products such as HALS Studio.
* Cleanup applies to the account running the uninstaller. If other Windows
  accounts used a machine-wide installation, their private data is not deleted
  by another account's uninstall. Windows-managed execution history, security
  logs, and restore points are outside the application's uninstall scope.
* Run `powershell -NoProfile -ExecutionPolicy Bypass -File
  packaging/verify_uninstall.ps1 -Compiler path\to\ISCC.exe` to test the real
  cleanup rules using a tiny installer with isolated folders and registration.
  The test checks that saved projects and unrelated files survive. CI runs it
  before compiling the production installer.
