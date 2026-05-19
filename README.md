# HALS — Holographic Acoustic Loudspeaker Scanner

<p align="center">
  <img src="images/splash.png" alt="Holographic Acoustic Loudspeaker Scanner" width="600"/>
</p>

A Python-based **Near Field Scanner** for automated acoustic impulse response measurements. The system orchestrates a 3-axis CNC rig (typically GRBL or FluidNC based) 
to position a microphone around an acoustic source (e.g., a loudspeaker) while precisely synchronizing audio playback and capture.

The scanner supports multiple coordinate systems and scanning patterns through a flexible plugin architecture, making it suitable for both cylindrical and spherical near-field measurements.

For a detailed walkthrough of the system and the graphical interface, please refer to the [HarmonicDrive User Guide](#harmonicdrive-user-guide) section below.

> **History:** The initial implementation was written in Octave. Although it worked well as a proof-of-concept, Python proved to be a more versatile platform for hardware control, signal processing, and extensibility.

---

## ✨ Features

- **Automated scanning** — define a set of measurement positions and let the scanner work through them unattended.
- **Real-time Progress Monitoring** — stay informed with point-by-point updates (e.g., "Measuring point 10 of 200... 5% complete") logged during long-running scans.
- **Cylindrical & spherical grids** — built-in plugins for cylindrical, spherical, arc-based, and file-based measurement point generation.
- **Impulse response capture** — uses an in-project exponential sine sweep engine with loopback-marker alignment and FFT deconvolution for high-quality IR measurements.
- **GRBL / FluidNC motion control** — communicates with Arduino or ESP32-based CNC controllers over serial.
- **DSP Backend** — Includes custom sweep generation, Barker-code loopback alignment, multi-sweep averaging, FFT deconvolution, driver-protection filtering, linear/distortion IR separation, and windowing.
- **DSP Verification Tooling** — Automated real-time verification of measurement quality, including SNR estimation, THD calculation, and alignment Peak Sharpness Ratio (PSR) monitoring.
- **Pluggable architecture** — measurement-point generators are loaded as plugins; easy to add your own.
- **Configurable via INI file** — all hardware, audio, and motion parameters live in a single `config.ini`.
- **Mock Mode** — test your measurement sequences without hardware using the built-in mock interfaces for both motion and audio.

---

## 📂 Project Structure

```text
NFS/
├── config.ini          # Main application configuration
├── images/             # Documentation images
├── src/
│   ├── harmonic_drive/ # GUI Application (HarmonicDrive)
│   │   ├── gui.py             # Main GUI entry point
│   └── nfs/            # Core Library
│       ├── audio.py           # Audio capture and DSP
│       ├── datatypes.py       # Shared data structures
│       ├── factory.py         # Plugin and component factories
│       ├── grbl_controller.py # Interface to GRBL hardware
│       ├── loader.py          # Dynamic plugin loader
│       ├── motion_manager.py  # High-level motion orchestration
│       ├── nfs.py             # Main NearFieldScanner logic
│       ├── scanner.py         # Low-level CNC axis control
│       └── plugins/           # Measurement point generator plugins
└── tests/              # Comprehensive test suite
```


---

## 🚀 Getting Started

### Prerequisites

| Requirement | Details |
|---|---|
| **Python** | 3.13.5 |
| **uv** | Package manager ([install guide](https://docs.astral.sh/uv/getting-started/installation/)) |
| **Hardware** | A GRBL/FluidNC-controlled CNC frame with at least two linear axes and one rotational axis, plus an audio interface |

### Installation

Clone the repository
```
git clone https://github.com/TomKamphuys/NFS.git
cd NFS
```

Install dependencies (including dev tools)
```
uv sync --all-groups
```

or without dev tools
```
uv sync --no-dev
```

### Launch the UI
```bash
uv run harmonic-drive
```
The UI will be automatically opened. In case it isn't, it is accessible at `http://localhost:8080`.


## Use Pycharm (currently mostly used as development is still causing rapid changes)

Welcome to PyCharm! Integrating `uv` into your workflow is a great choice for fast, reliable dependency management. Since you’ve already installed PyCharm, here is the most efficient, step-by-step path to cloning your repository and setting up your environment.

---

### Step 1: Clone Your Repository
1.  Open **PyCharm**.
2.  On the Welcome screen, select **Get from VCS** (Version Control System). If you are already inside a project, go to the top menu: `File` > `New` > `Project from Version Control...`.
3.  Paste your GitHub repository URL into the **URL** field.
4.  Choose the local folder where you want to save the project and click **Clone**.
5.  PyCharm will ask if you want to open the project in the current window or a new one; select the option that best fits your preference.

### Step 2: Initialize the Environment with `uv`
Once the project is open, you need to ensure your dependencies are installed. PyCharm often detects `pyproject.toml` files, but using `uv` directly in the terminal is the most reliable way to sync your environment.

1.  Open the **Terminal** tab at the bottom of the PyCharm window (`Alt+F12` on Windows/Linux or `Option+F12` on macOS).
2.  Run the following command:
    ```bash
    uv sync
    ```
    * **Why?** This command reads your `uv.lock` file and creates or updates a `.venv` folder in your project directory with all the exact package versions required.

### Step 3: Configure the Interpreter in PyCharm
After `uv sync` finishes, you need to tell PyCharm to use that environment as the "Python Interpreter" so it can provide code completion, error checking, and debugging.

1.  Open **Settings** (`Ctrl+Alt+S` or `Cmd+,` on macOS).
2.  Navigate to **Project: [Your Project Name]** > **Python Interpreter**.
3.  Click the **Add Interpreter** link (or the gear icon) and select **Add Local Interpreter...**.
4.  In the dialog:
    * Select **Virtualenv Environment** on the left.
    * Select **Existing environment**.
    * Click the `...` button and browse to the `.venv` folder that `uv sync` created in your project directory.
    * Select the `python` executable inside the `bin` (or `Scripts` on Windows) folder of that `.venv`.
5.  Click **OK** to apply the changes.

---

### Pro-Tips for `uv` + PyCharm
* **Automatic Detection:** Newer versions of PyCharm may automatically prompt you to "Create a uv environment" as soon as you open the project. If you see this notification in the bottom-right corner or at the top of the editor, click it! It will handle the syncing and interpreter configuration for you.
* **Running Scripts:** Once the interpreter is set, you can run your code by right-clicking any `.py` file and selecting **Run 'filename'**. PyCharm will now correctly resolve all your dependencies.
* **Managing Dependencies:** If you need to add a new package later, don't just run `pip install`. Use the terminal to keep your lockfile updated:
    ```bash
    uv add <package_name>
    ```
    PyCharm will automatically detect that the environment has changed and update its index.


## 📚 Documentation

The project uses Sphinx to automatically generate API documentation from docstrings.

### Building Locally
To build the HTML documentation locally, run:
```bash
uv run sphinx-build -b html docs docs/_build/html
```
The output will be available in `docs/_build/html/index.html`.

### Logging Configuration
Logging is centralized and can be configured in `config.ini`:
```ini
[logging]
level = INFO
file = scanner.log
rotation = 10 MB
retention = 1 week
```
- `level`: DEBUG, INFO, WARNING, ERROR, CRITICAL
- `file`: Path to the log file
- `rotation`: When to rotate the log (e.g., size or time)
- `retention`: How long to keep old logs

### Online Documentation
Documentation is automatically built and deployed to GitHub Pages on every push to the `master` branch.

---

## 🧪 Testing

Run the standard test suite:
```bash
uv run pytest
```

### Full System Mock Integration Test
A specialized integration test is available that simulates a complete measurement run using mock hardware (both audio and motion). This verifies the entire orchestration logic from plugin loading to final position logging.

```bash
uv run pytest tests/test_full_system_mock.py
```

### DSP Verification Tests
Run the dedicated DSP verification tests to validate the automated quality metrics (SNR, THD, PSR).

```bash
uv run pytest tests/test_dsp_verification.py
```

---

## 🔌 Plugins

Measurement-point generators are loaded dynamically from `src/nfs/plugins/`. The following are included:

| Plugin | Description |
|---|---|
| `cylindrical_measurement_points` | Regular cylindrical grid |
| `spherical_measurement_points` | Regular spherical grid |
| `spherical_measurement_points_sorted` | Spherical grid, sorted for minimal travel |
| `spherical_measurement_points_arcs` | Spherical grid using arc moves |
| `spherical_measurement_points_arcs_random` | Spherical grid with randomised arc ordering |
| `file_measurement_points` | Load positions from a CSV file |

To add a custom plugin, create a new module in `src/nfs/plugins/` that implements the `MeasurementPoints` protocol (see `src/nfs/measurement_points.py`) and 
include a `register` function. Then, register it in `config.ini` under the `[plugins]` section.

### Example Plugin Structure
```python
from nfs.datatypes import CylindricalPosition

class MyCustomPoints:
    def __init__(self, some_param):
        self.some_param = some_param
        self._ready = False

    def next(self) -> CylindricalPosition:
        # Calculate and return next position
        return CylindricalPosition(r=100, t=45, z=50)

    def ready(self) -> bool:
        return self._ready

    def reset(self) -> None:
        pass

def register(factory) -> None:
    factory.register("MyCustomPoints", MyCustomPoints)
```

---

## 🏗️ Hardware 

N.B. A new and improved setup has been designed and built. Please see DiyAudio thread for more details.


---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📄 License

Proprietary — see `pyproject.toml` for details.
All rights reserved.



# HarmonicDrive User Guide

HarmonicDrive is a near-field acoustic scanner controller featuring a real-time web-based UI. It automates loudspeaker 
measurements by moving a microphone along predefined grids (e.g. cylindrical or spherical) using a 3-axis CNC-style turntable/arm.

---

## 1. Getting Started


## Prerequisites
1.  The grbl settings have been set correctly using external tooling (e.g. IOSender).

### Installation
1.  **Clone Repository**:
    ```bash
    git clone https://github.com/TomKamphuys/NFS.git
    cd NFS
    ```
2.  **Sync Dependencies**: Use `uv` to manage the environment.
    ```bash
    uv sync --all-groups
    ```
3.  **Launch the UI**:
    ```bash
    uv run harmonic-drive
    ```
    The UI will be automatically opened. In case it isn't, it is accessible at `http://localhost:8080`.

---

## 2. Configuration (`config.ini`)

The `config.ini` file controls all hardware and software parameters.

### `[scanner]`
- `controller`: Type of motion controller (typically `grbl_streamer`).
- `feed_rate`: Global movement speed limit in mm/min.

### `[motion_manager]`
- `type`: The class name for motion logic (e.g., `CylindricalMeasurementMotionManager`).
- `measurement_points`: Reference to the section defining the grid.
- `safe_radius`: Minimum distance maintained to prevent collisions.

### `[audio]` & `[sweep]`
- `mode`: Audio backend entry point, for example `mock_interface` for testing without hardware.
- `in_dev` / `out_dev`: Audio interface device indices.
- `in_ch_mic` / `in_ch_loop`: Input channels for the microphone and loopback marker.
- `out_ch_spkr` / `out_ch_ref`: Output channels for the speaker sweep and reference marker.
- `fs`: Sample rate in Hz.
- `sweep_dur_s`: Length of the exponential sine sweep.
- `sweep_level_dbfs`: Playback level for the sweep and marker.
- `num_sweeps`: Number of captures to average per point to improve SNR.
- `align_to_first_marker`: If true, aligns subsequent sweeps from the first marker; if false, re-syncs every sweep.
- `pre_sil_ms` / `post_sil_ms`: Silence around the sweep for settling and decay capture.
- `mic_tail_taper_ms`: Fade applied to the captured tail.
- `protect_hpf_hz` / `protect_hpf_order`: Optional playback high-pass filter for driver protection.
- `naming_convention`: File naming format for recordings (`tom` or `dimitri`).

---

## 3. The GUI Interface

The UI is divided into two main panels: **Controls (Left)** and **Plots (Right)**.

### Jog Controls
- **PHI (Rotation)**: Rotates the turntable. Buttons are labeled with step sizes (1, 10, 60, 120 degrees). `CW` (Clockwise) and `CCW` (Counter-Clockwise).
- **R (Radius)**: Moves the arm in/out. `IN` moves towards the center, `OUT` moves away.
- **Z (Height)**: Moves the microphone up/down.
- **STOP (HOLD)**: Red button in the center of each jog row to immediately halt that axis.

### System Commands
- **HOME**: Initiates the hardware homing sequence. Turns **Green** when successful, **Orange** if homing is required.
- **Clear Alarm**: Resets the GRBL "Alarm" state (often triggered by hitting limit switches).
- **Soft Reset**: Resets the GRBL controller firmware.
- **REHOME**: Forces a re-homing sequence. This is useful if the GRBL firmware is stuck in an alarm state.
- **HOLD**: Immediate pause for all motion.

### Setup & Measurement
- **Height Offset**: Enter the distance (mm) from the turntable stool to the speaker's acoustic center.
- **Set height offset**: Applies the value to the current coordinate system.
- **Zero NFS**: Critical step. Sets the current position as the "Zero" reference and applies the height offset.
- **Start measurements**: Begins the automated scan through all grid points.
- **Take single measurement**: Captures a single sweep at the current position.

### Live Displays
- **Position Dials**: Real-time readout of Radius (R), Phi (P), and Height (Z).
- **Status**: Current machine state (Idle, Run, Alarm, etc.).
- **Live Capture**: Plotly panels for measurement progress, measured positions, frequency response, and impulse response. Panels can be reordered, shown/hidden, and saved as defaults.
- **Log View**: Accessible via **Show Logs**. Displays real-time system events and errors.

---

## 4. Recommended Workflow

Follow these steps for a successful measurement session:

1.  **Hardware Prep**: Mount the speaker on the turntable and the microphone on the arm. Make sure everything is properly aligned. Etc, etc.
2.  **Home the System**: Click **HOME** and wait for the button to turn green and the status to show `IDLE`.
3.  **Manual Alignment**:
    - Use the Jog buttons to move the microphone until it is perfectly aligned with the zero-triangle.
4.  **Set Reference**:
    - Enter the **Height Offset** (distance from the stool surface to the reference point).
    - Click **Zero NFS**. The coordinate system will now center on your speaker.
5.  **Run Scan**:
    - Click **Start measurements**. 
    - Monitor the **Live Plot** and **Log View** to ensure measurements are proceeding as expected.
    - WAV files will be saved to the `measurements/` folder automatically with the time and date encoded in the directory.

---

## 5. Troubleshooting

- **Machine in ALARM**: Usually caused by hitting a limit switch or a hard stop. Click **Clear Alarm**. If it persists, use **Soft Reset**.
- **Audio Errors**: Check the `[audio]` section in `config.ini`. Ensure the `in_dev` and `out_dev` match the indices found by running `uv run harmonic-drive --list-devices` (or similar utility).
- **Unexpected Movement**: Verify `steps_per_millimeter` in the configuration. Check if the axes are reversed in the GRBL settings.
- **UI Unresponsive**: Refresh the browser page. The backend `main.py` should remain running.
