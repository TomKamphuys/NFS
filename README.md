# HALS — Holographic Acoustic Loudspeaker Scanner

<p align="center">
  <img src="images/splash.png" alt="Holographic Acoustic Loudspeaker Scanner" width="600"/>
</p>

A **Near Field Scanner** for automated acoustic impulse-response measurements. It drives a
3-axis CNC rig (GRBLHAL based) to move a microphone around an acoustic source (e.g., a
loudspeaker) while precisely synchronizing audio playback and capture.

Everything is done from a bundled desktop application (**HarmonicDrive**, built with Qt/PySide6).
All hardware, audio, and motion parameters are configured in the graphical interface — **you never
have to edit files by hand**.

> **New here?** On Windows, just download and run the installer — **no Python, uv, or Git
> required**. See [Install & run (Windows)](#-install--run-windows) below.

---

## ⚠️ Safety Warning — Moving Parts

**This software controls physical machinery that can move without warning and cause serious injury.**

The scanner drives a motorized 3-axis CNC rig. Moving axes, belts, and the microphone
carriage can crush, pinch, or trap fingers, hands, hair, clothing, and cables. Before running
anything that moves the hardware:

- **Keep clear of the machine while it is powered and moving.** Never reach into the travel envelope
  during homing, jogging, or a scan.
- **Always have a working emergency stop** within reach and test it before starting.
- **Homing and jogging move the axes immediately** — including at startup. Confirm the machine is
  clear first.
- **Verify limits and workspace** so the machine cannot drive into itself, the loudspeaker, or a person.
- **Secure loose items** (cables, tools, hair, clothing).
- **Do not leave running scans unattended** unless the machine is safely enclosed.

> **No warranty / no liability.** This software is provided "as is" under the MIT License, **without
> any warranty** and **without any liability** for damage or injury. You are solely responsible for
> operating your hardware safely. See the [LICENSE](LICENSE) file.

---

## 🪟 Install & run (Windows)

**This is all most users need.**

1. Go to the [GitHub Releases](https://github.com/TomKamphuys/NFS/releases) page.
2. Download the latest **`HALS-Near-Field-Scanner-Setup-<version>.exe`**.
3. Run it and follow the wizard. It bundles Python and every dependency, so there is nothing else to
   install.
4. Launch **HALS Near Field Scanner** from the Start menu (or the desktop shortcut, if you enabled it).

The application opens and all configuration is done from within the GUI — continue with the
[User Guide](#-user-guide) below.

> Windows may show a SmartScreen warning because the installer is not code-signed. Choose
> **More info → Run anyway**.

---

## ✨ What it does

- **Automated scanning** — define measurement positions and let the scanner work through them unattended.
- **Everything in the GUI** — machine control, audio setup, grid generation, live capture, and all
  settings. No manual file editing.
- **Projects** — each measurement session lives in its own folder (configuration, grids, recordings).
- **Cylindrical & spherical grids** — built-in generators (regular, arc-based, sorted, or loaded from a CSV).
- **High-quality impulse responses** — exponential sine-sweep engine with loopback-marker alignment,
  multi-sweep averaging, and FFT deconvolution.
- **Live quality metrics** — real-time SNR, THD, and alignment (PSR) monitoring.
- **GRBLHAL motion control** over serial (BTT skr ez 3).
- **Mock mode** — rehearse a full measurement run without any hardware connected.

---

# 📖 User Guide

HarmonicDrive is the bundled desktop controller. It automates loudspeaker measurements by moving a
microphone along a grid (cylindrical or spherical) using a 3-axis CNC-style turntable/arm.

## 1. Before you start

1. The GRBLHAL settings are configured with external tooling (e.g., IOSender).
2. An audio interface with at least one input and one output channel is connected.

On the first launch you'll be prompted to review the audio and scanner settings; if anything required is
missing, the app opens the relevant view automatically.

## 2. Configuration (all in the GUI)

Settings are saved automatically with the active project — no files to edit.

**Settings dialog** (from the left-hand menu), grouped into tabs:

- **Scanner** — motion controller type and global feed rate (mm/min).
- **Motion manager** — motion logic (cylindrical/spherical), the measurement grid, and the safe
  radius used to prevent collisions.
- **Measurement points** — choose the grid generator and edit its parameters.
- **Audio & sweep** — sample rate, sweep duration/level, sweeps to average, padding, tail taper,
  driver-protection high-pass filter, marker alignment, and recording naming.
- **App / logging** — interface preferences and logging level.

Use **Restore Defaults** to reset a section; **Apply / Save** to persist changes.

**Audio Setup pane** — dedicated screen for the audio API, input/output devices, and the microphone,
loopback, speaker, and reference channels. Visit this first if you see an audio-related startup warning.

**Projects** — each project is a folder holding its own configuration, grids, and recordings. When no
folder is selected, the app uses a temporary working project.

## 3. The interface

The main window has a collapsible **Views** menu on the left:

- **Audio Setup** — configure the interface and channels.
- **Grid Generator** — create and preview measurement grids.
- **Machine Control** — jog, home, and start measurements.
- **Live Capture** — real-time plots of progress, positions, frequency response, and impulse response.
- **Settings** — the configuration dialog above.
- **Shutdown Program** — closes the application.

**Machine Control** highlights:

- **Jog** the **PHI** (rotation), **R** (radius), and **Z** (height) axes with the step buttons.
- **Home / Rehome**, **Clear Alarm / Soft Reset**, and **Hold / Stop**.
- **Height offset / Zero NFS** — set the coordinate-system reference on the speaker's acoustic center.
- **Start measurements / Take single measurement**.

## 4. Recommended workflow

1. **Hardware prep** — mount and align the speaker and microphone.
2. **Select / create a project** — keep configuration, grids, and recordings together.
3. **Configure audio** — pick interface, devices, and channels in **Audio Setup**.
4. **Generate a grid** — in **Grid Generator**, or select an existing grid in **Settings**.
5. **Home the system** — click **HOME** and wait for the `IDLE` state.
6. **Set reference** — jog to the zero-triangle, enter the **Height Offset**, and click **Zero NFS**.
7. **Run scan** — click **Start measurements** and watch **Live Capture** and the **Log View**. WAV
   files are saved to the project's recordings folder automatically (time/date encoded in the directory).

## 5. Troubleshooting

- **Machine in ALARM** — usually a limit switch or hard stop. Click **Clear Alarm**; if it persists,
  **Soft Reset**.
- **Audio errors** — open **Audio Setup** and confirm the API, devices, and channels.
- **Unexpected movement** — check steps-per-mm and feed rate in **Settings**, and whether axes are
  reversed in the GRBL settings.
- **Startup warnings** — the app opens **Audio Setup** or **Settings** when required parameters are
  missing; review and save the highlighted section.

---

# 🛠️ For developers

Most of this is standard; only the non-obvious bits are called out.

### Run from source

Requires **Python 3.13.5** and **[uv](https://docs.astral.sh/uv/getting-started/installation/)**.

```bash
git clone https://github.com/TomKamphuys/NFS.git
cd NFS
uv sync --all-groups          # or: uv sync --no-dev
uv run harmonic-drive-qt      # optional: --config path\to\config.ini
```

> **PyCharm:** it usually offers to *create a uv environment* on open — accept it, and PyCharm handles
> the sync and interpreter automatically. Otherwise, run `uv sync` and point the interpreter at the
> generated `.venv`.

### Project layout

```text
NFS/
├── config.ini                # Default configuration (managed through the GUI)
├── src/
│   ├── harmonic_drive_qt/     # Native Qt (PySide6) desktop GUI — HarmonicDrive
│   ├── grid_generator/        # Grid generation helpers and assets
│   └── nfs/                   # Core library (audio DSP, motion, plugins, scanner)
│       └── plugins/           # Measurement-point generator plugins
└── tests/                     # Test suite
```

### Testing

```bash
uv run pytest
uv run pytest tests/test_full_system_mock.py   # full mock hardware run
uv run pytest tests/test_dsp_verification.py   # DSP quality metrics (SNR, THD, PSR)
```

### Documentation

```bash
uv run sphinx-build -b html docs docs/_build/html
```

Docs are auto-deployed to GitHub Pages on every push to `master`.

For a high-level tour of how the pieces fit together — component overview, class
diagrams and sequence diagrams — see [`docs/architecture.md`](docs/architecture.md)
(the Mermaid diagrams render directly here on GitHub and on the docs site next to
the API reference).

### Plugins

Measurement-point generators live in `src/nfs/plugins/` and are loaded dynamically via
`importlib.metadata` entry points. Included generators: cylindrical, spherical (regular / sorted /
arc / random-arc), and file-based (CSV).

To add one, create a module implementing the `MeasurementPoints` protocol (see
`src/nfs/measurement_points.py`) with a `register(factory)` function:

```python
from nfs.datatypes import CylindricalPosition

class MyCustomPoints:
    def __init__(self, some_param):
        self.some_param = some_param
        self._ready = False

    def next(self) -> CylindricalPosition:
        return CylindricalPosition(r=100, t=45, z=50)

    def ready(self) -> bool:
        return self._ready

    def reset(self) -> None:
        pass

def register(factory) -> None:
    factory.register("MyCustomPoints", MyCustomPoints)
```

It then appears in the **Settings** dialog.

### CI / releases — the non-obvious parts

- Four workflows run in `.github/workflows/`: tests (`python-package.yml`), docs (`docs.yml`),
  Python package (`release.yml`), and the **Windows installer** (`windows-installer.yml`).
- Pushing to `master` builds the installer as a **downloadable Actions artifact only** — it is *not*
  attached to the Releases page.
- To publish for end users, **cut a release** by pushing a `v*` tag:
  ```bash
  # bump version in pyproject.toml first
  git tag v0.2.1
  git push origin v0.2.1
  ```
  This builds and attaches `nfs-<version>-py3-none-any.whl`, `nfs-<version>.tar.gz`, and
  `HALS-Near-Field-Scanner-Setup-<version>.exe` to the GitHub Release.
- The installer is an Inno Setup wrapper (`packaging/installer.iss`) around a PyInstaller **onedir**
  build (`packaging/hals.spec`). To build locally: `uv run pyinstaller packaging/hals.spec --noconfirm`
  then `iscc /DMyAppVersion=0.2.0 packaging\installer.iss`.

> This project is **not published to PyPI**, so `pip install nfs` will not work. Install from a
> Release wheel or `pip install git+https://github.com/TomKamphuys/NFS.git`.

> **History:** the original proof-of-concept was written in Octave; it was ported to Python for
> better hardware control, DSP, and extensibility. The GUI, once a separate *HarmonicDrive*
> repository, has been rewritten as a native Qt app and merged here. The old repository is no longer used.

---

## 🤝 Contributing

Contributions are welcome. For major changes, please open an issue first to discuss what you'd like to
change. Fork, create a feature branch, commit, push, and open a Pull Request.

## 📄 License

Released under the [MIT License](LICENSE). The software is provided "as is", **without warranty of any
kind and without any liability**. Because it controls machinery with moving parts, please also read the
[Safety Warning](#️-safety-warning--moving-parts) above — you are solely responsible for operating your
hardware safely.
