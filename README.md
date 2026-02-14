AI Generated:

# NFS — Near Field Scanner

<p align="center">
  <img src="images/NFS.png" alt="Near Field Scanner" width="600"/>
</p>

A Python-based **Near Field Scanner** for automated acoustic impulse response measurements. The system drives a GRBL/FluidNC-controlled CNC rig (with both linear and rotational axes) while synchronizing audio capture, allowing you to map the sound field around a loudspeaker or other acoustic source.

> **History:** The initial implementation was written in Octave. Although it worked well as a proof-of-concept, Python proved to be a more versatile platform for hardware control, signal processing, and extensibility.

---

## ✨ Features

- **Automated scanning** — define a set of measurement positions and let the scanner work through them unattended.
- **Cylindrical & spherical grids** — built-in plugins for cylindrical, spherical, arc-based, and file-based measurement point generation.
- **Impulse response capture** — uses exponential sweep excitation with [pyfar](https://pyfar.org/) for high-quality IR measurements.
- **GRBL / FluidNC motion control** — communicates with Arduino or ESP32-based CNC controllers over serial.
- **Pluggable architecture** — measurement-point generators are loaded as plugins; easy to add your own.
- **Configurable via INI file** — all hardware, audio, and motion parameters live in a single `config.ini`.

---

## 📂 Project Structure

TODO


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
git clone https://github.com/<your-username>/NFS.git cd NFS
Install dependencies (including dev tools)
uv sync --all-groups


### Configuration

Edit `config.ini` to match your hardware setup. Key sections:

| Section | Purpose |
|---|---|
| `[scanner]` | GRBL controller type and feed rate |
| `[grbl_x/y/z_axis]` | Steps/mm, max rate, acceleration per axis |
| `[audio]` | Audio device, sweep count, mock mode |
| `[sweep]` | Sweep type, sample rate, frequency range, duration |
| `[motion_manager]` | Coordinate system, measurement-point plugin, safe radius |
| `[windows]` | Serial port (COM port) |

### Running

Run the application
uv run nfs-app


---

## 🧪 Testing

bash uv run pytest


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

To add a custom plugin, create a new module in the `plugins/` directory and register it in `config.ini` under the `[plugins]` section.

---

## 🏗️ Hardware 

N.B. A new and improved setup has been designed and built. Please see DiyAudio thread for more details.

First prototype:
See [`Documents/BuildDescription.md`](Documents/BuildDescription.md) for the bill of materials and mechanical assembly instructions.

---

## 📄 License

Proprietary — see `pyproject.toml` for details.



