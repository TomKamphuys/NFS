# Project Guidelines & Overview — NFS (HALS)

## Project Overview
**HALS (Holographic Acoustic Loudspeaker Scanner) / NFS (Near Field Scanner)** is an automated acoustic impulse-response measurement system. It controls a 3-axis CNC hardware rig (GRBLHAL / ESP32 based) to move a microphone around an acoustic source (such as a loudspeaker) along cylindrical or spherical grids while precisely synchronizing audio playback (exponential sine sweeps) and capture to record high-quality acoustic impulse responses (IRs).

The primary user interface is **HarmonicDrive**, a desktop application built with Python and Qt (PySide6) / PyVista.

---

## Architecture & Codebase Structure

The codebase is organized in `src/` under three main packages:

1. **`src/nfs/` (Core Scanner Engine)**:
   - `nfs.py` (`NearFieldScanner`): Central orchestrator class tying together motion management, coordinate scanning, audio sweep execution, pausing/resuming, and progress reporting.
   - `scanner.py` (`Scanner`): High-level coordinate-aware motion API (radial, angular, vertical, and arc moves) wrapping the low-level GRBL controller.
   - `grbl_controller.py`: Low-level GRBL communication protocol and controller implementations (`IGrblController`, `ESP32Duino`, mocks for tests).
   - `motion_manager.py`: Motion planning abstractions (`IMotionManager`, `CylindricalMeasurementMotionManager`, `FastCylindricalMeasurementMotionManager`, `SphericalMeasurementMotionManager`) ensuring collision-free paths.
   - `measurement_points.py` & `plugins/`: Point generation strategies for measurement grids (`cylindrical`, `spherical`, `spherical_arcs_random`, `file`).
   - `audio.py`: Audio subsystem (`IAudio`, `Audio`, `MockInterfaceAudio`) responsible for sweep generation, loopback synchronization, sound device I/O, and FFT deconvolution.
   - `factory.py`, `registry.py`, `loader.py`: Extensible plugin registration and instantiation based on configuration.
   - `datatypes.py`, `utils/`: Coordinate models (`CylindricalPosition`), DSP utilities (exponential sweep, deconvolution, filtering), and geometry helpers.

2. **`src/harmonic_drive_qt/` (Desktop GUI Application)**:
   - PySide6 desktop interface for live machine control, 3D trajectory and point preview (PyVista / pyvistaqt / matplotlib), audio setup, and project session management.
   - Entry point: `harmonic_drive_qt.main:main` (CLI command: `harmonic-drive-qt`).

3. **`src/grid_generator/`**:
   - Helper utilities and UI components for grid visualization and generation.

---

## Development & Test Guidelines

### Dependencies & Environment
- Python version: Python 3.13 (see `pyproject.toml`).
- Package manager / tooling: `uv` / standard `pip` / `setuptools`.
- Core dependencies include `numpy`, `scipy`, `pandas`, `matplotlib`, `sounddevice`, `soundfile`, `pyside6`, `pyvista`, `pyvistaqt`, `grbl-streamer`, and `loguru`.

### Testing
- Test suite is located in the `tests/` directory.
- Test runner: `pytest`
- Run all tests:
  ```powershell
  pytest
  ```
- Run targeted tests:
  ```powershell
  pytest tests/test_audio_engine.py
  ```
- Mock implementations (`MockInterfaceAudio`, `GrblControllerMock`) exist across the codebase to allow hardware-independent test execution.

# Coding Guidelines for Junie

## General principles
- Inspect existing code and follow its established architecture and naming conventions before editing.
- Make the smallest change that fully solves the stated problem.
- Do not refactor, rename, reorganize, or add abstractions unless explicitly requested.
- Do not introduce new dependencies without asking.
- Do not duplicate existing functionality. Reuse existing components and utilities.

## Before implementation
- Identify the relevant files, existing behavior, and affected interfaces.
- For non-trivial changes, propose a concise plan and identify important edge cases before editing.
- If requirements conflict, or a key detail is ambiguous, ask rather than guess.

## Design and architecture
- Prefer high cohesion, low coupling, and clear separation of responsibilities.
- Keep domain logic independent of GUI code, hardware I/O, and presentation where practical.
- Keep state transitions explicit and enforce invariants at the appropriate layer.
- Avoid unnecessary indirection, premature generalization, and oversized classes or functions.

## Safety and hardware
- Treat machine motion, homing, coordinate validity, and collision avoidance as safety-critical.
- Never weaken travel limits, no-fly-zone constraints, or path validation to make a test pass.
- Validate the complete movement path, not only its destination.
- On uncertain machine position, fail safely and require explicit recovery.

## Python and signal processing
- Follow existing Python style, typing, and error-handling conventions.
- Keep DSP algorithms deterministic and separate from hardware and GUI operations.
- Preserve units, coordinate conventions, signal lengths, timing assumptions, and numerical precision.
- Handle invalid inputs and boundary conditions explicitly.

## Testing and verification
- Add or update focused tests for changed behavior and important edge cases.
- Prefer existing test frameworks and patterns.
- Run relevant tests and report the actual results. Never claim tests passed without running them.
- Do not change a test merely to hide a regression.
- Inspect the final diff for unrelated changes and unintended behavior.

## Scope control
- Change only files necessary for the task.
- Ask before expanding scope, changing public interfaces, or making architectural decisions not specified in the request.
- Do not silently discard existing functionality.
- At completion, summarize changes, tests run, and remaining risks or unverified assumptions.
