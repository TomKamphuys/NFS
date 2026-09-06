"""Qt-local config schema and persistence helpers.

This module contains the framework-agnostic parts that the native Qt UI needs
from the legacy Harmonic Drive config editor.
"""
from __future__ import annotations

import configparser
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from loguru import logger

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# Each entry: (key, kind, tooltip, options_or_none)
# kind in {"str", "int", "float", "bool", "choice", "opt_float"}
SchemaEntry = Tuple[str, str, str, Optional[List[str]]]

DISPLAY_LABELS = {
    "level": "Terminal verbosity",
    "show_machine_coordinate_system": "Show machine coordinates",
    "show_rehome_button": "Show ReHome button",
    "show_height_offset_controls": "Show height offset controls",
    "default_project_dir": "Default session folder",
    "cal_tool_height": "Calibration tool height (mm)",
    "feed_rate": "Feed rate (mm/min)",
    "safe_radius": "Safe radius (mm)",
    "homing_gap": "Homing gap (degrees)",
    "pole_gap": "Pole gap (mm)",
    "cap_spacing": "Cap spacing (mm)",
    "wall_spacing": "Wall spacing (mm)",
    "radius": "Radius (mm)",
    "height": "Height (mm)",
    "speaker_height": "Speaker height (mm)",
    "speaker_width": "Speaker width (mm)",
    "speaker_depth": "Speaker depth (mm)",
    "use_alternative_motion_controls": "Use alternative motion controls",
}

# ---------------------------------------------------------------------------
# Type-driven schemas for the polymorphic motion_manager / measurement-points
# pair.
#
# MOTION_MANAGER_TYPES maps a motion-manager class name to its extra
# (type-specific) fields. The base fields ('type', 'measurement_points')
# are added implicitly. Only the two factory-supported classes are listed
# here on purpose.
#
# MEASUREMENT_POINTS_TYPES maps a MeasurementPoints class name to the
# constructor arguments it accepts (besides 'type'). Mirrors the
# __init__ signatures of the plugins in nfs.plugins.*.
# ---------------------------------------------------------------------------
MOTION_MANAGER_TYPES: Dict[str, List[SchemaEntry]] = {
    "CylindricalMeasurementMotionManager": [
        ("safe_radius", "optional_float",
         "Optional extra-safe radius (mm) the motion manager retracts to before slewing. "
         "Leave empty to default to 0 and avoid extra safe-radius intervention.", None),
    ],
    "SphericalMeasurementMotionManager": [
        # No extra parameters beyond the base ones.
    ],
}

MEASUREMENT_POINTS_TYPES: Dict[str, List[SchemaEntry]] = {
    "FileMeasurementPoints": [
        ("filename", "str",
         "CSV file containing the measurement grid points.", None),
        ("homing_gap", "optional_float",
         "Optional extra gap (degrees) excluded around the homing position.", None),
        ("pole_gap", "optional_float",
         "Optional extra gap (mm) excluded around the speaker stand/pole.", None),
    ],
    "CylindricalMeasurementPoints": [
        ("nr_of_angular_points", "int", "Number of angular sample points.", None),
        ("nr_of_radial_cap_points", "int", "Number of radial points on the caps.", None),
        ("nr_of_vertical_points", "int", "Number of vertical (z) sample points.", None),
        ("cap_spacing", "float", "Spacing of cap points (mm).", None),
        ("wall_spacing", "float", "Spacing between points and the wall (mm).", None),
        ("radius", "float", "Cylinder radius (mm).", None),
        ("height", "float", "Cylinder height (mm).", None),
    ],
    "SphericalMeasurementPoints": [
        ("nr_of_points", "int", "Total number of measurement points.", None),
        ("wall_spacing", "float", "Spacing between points and the wall (mm).", None),
        ("radius", "float", "Sphere radius (mm).", None),
    ],
    "SphericalMeasurementPointsArcs": [
        ("nr_of_points", "int", "Total number of measurement points.", None),
        ("wall_spacing", "float", "Spacing between points and the wall (mm).", None),
        ("radius", "float", "Sphere radius (mm).", None),
    ],
    "SphericalMeasurementPointsArcsRandom": [
        ("nr_of_points", "int", "Total number of measurement points.", None),
        ("wall_spacing", "float", "Spacing between points and the wall (mm).", None),
        ("radius", "float", "Sphere radius (mm).", None),
        ("homing_gap", "optional_float",
         "Optional extra gap (degrees) excluded around the homing position.", None),
        ("pole_gap", "optional_float",
         "Optional extra gap (mm) excluded around the speaker stand/pole.", None),
    ],
    "SphericalMeasurementPointsSorted": [
        ("nr_of_points", "int", "Total number of measurement points.", None),
        ("wall_spacing", "float", "Spacing between points and the wall (mm).", None),
        ("radius", "float", "Sphere radius (mm).", None),
        ("speaker_height", "float", "Speaker height (mm).", None),
        ("speaker_width", "float", "Speaker width (mm).", None),
        ("speaker_depth", "float", "Speaker depth (mm).", None),
    ],
}

# Static (non-polymorphic) sections.
EDITABLE_SCHEMA: Dict[str, List[SchemaEntry]] = {
    "audio": [
        ("mode", "choice",
         "Audio backend. 'hardware' uses real I/O; 'mock_interface' simulates a "
         "device with realistic latency and FIR; 'mock' is a trivial mock.",
         ["hardware", "mock_interface", "mock"]),
        ("in_dev", "int", "Input device index (see `python -m sounddevice`). ASIO recommended.", None),
        ("out_dev", "int", "Output device index (see `python -m sounddevice`).", None),
        ("in_ch_mic", "int",
         "Input channel for the measurement microphone (0-based for all audio APIs).", None),
        ("in_ch_loop", "int", "Input channel used as electrical loopback / reference.", None),
        ("out_ch_spkr", "int", "Output channel driving the speaker under test.", None),
        ("out_ch_ref", "int",
         "Output channel routed back into the loopback input for timing reference.", None),
        ("in_dev_name", "str", "Saved input device name used to recover from device ID changes.", None),
        ("in_dev_hostapi", "str", "Saved input host API used to disambiguate audio devices.", None),
        ("out_dev_name", "str", "Saved output device name used to recover from device ID changes.", None),
        ("out_dev_hostapi", "str", "Saved output host API used to disambiguate audio devices.", None),
        ("fs", "int", "Sample rate in Hz. Must match the device's configured rate.", None),
        ("blocksize", "int",
         "Audio buffer size in samples. Must match the ASIO panel if applicable.", None),
        ("wasapi_exclusive", "bool",
         "Request exclusive WASAPI mode. Only relevant on Windows when not using ASIO.", None),
    ],
    "sweep": [
        ("naming_convention", "choice",
         "'tom' uses (r,phi,z).wav; 'dimitri' uses <orderID>_r..ph..z.._ir.wav.",
         ["tom", "dimitri"]),
        ("sweep_dur_s", "float", "Duration of each exponential sine sweep, in seconds.", None),
        ("sweep_level_dbfs", "float",
         "Playback level in dBFS. Internally compensated so the IR retains level.", None),
        ("num_sweeps", "int", "Number of sweeps averaged per measurement point.", None),
        ("protect_hpf_hz", "opt_float",
         "Corner frequency of the protection HPF (Hz). Use 'None' to disable.", None),
        ("protect_hpf_order", "int", "Slope order of the protection HPF (1, 2, 3 or 4).", None),
        ("protect_hpf_correction", "bool",
         "Enable inverse filtering during IR generation to correct the protection HPF.", None),
        ("protect_hpf_corr_db_cap", "float",
         "Maximum gain boost, in dB, allowed for the HPF correction.", None),
        ("align_to_first_marker", "bool",
         "If True, fixed timing relative to the first marker; if False, resync each sweep.", None),
        ("pre_sil_ms", "float", "Silence before the sweep, in ms (lets the driver settle).", None),
        ("post_sil_ms", "float", "Silence after the sweep, in ms (captures the reverb tail).", None),
        ("mic_tail_taper_ms", "float",
         "Short fade-out at end of capture, in ms (suppresses DC/noise).", None),
        ("debug_saves", "bool",
         "If True, save raw loopback / reference / mic to the measurement debug folder.", None),
        ("h2_test_db", "opt_float",
         "Inject 2nd harmonic at this dB level for distortion tests. 'None' to disable.", None),
        ("h3_test_db", "opt_float",
         "Inject 3rd harmonic at this dB level for distortion tests. 'None' to disable.", None),
    ],
    "scanner": [
        ("verify_controller_on_connect", "bool",
         "Require a GRBL acknowledgement when opening the scanner. Disable temporarily to diagnose reconnect/probe failures; an open port does not guarantee motion commands will work.", None),
        ("feed_rate", "int",
         "GRBL feed rate (mm/min) used for moves between measurement points.", None),
        ("cal_tool_height", "float",
         "Height of the calibration tool used when setting WCS zero. The current "
         "machine height is assigned this value, placing WCS Z0 below it.",
         None),
    ],
    "grbl_streamer": [
        ("type", "choice",
         "GRBL controller backend. Arduino connects to the configured COM port; "
         "Mock simulates the controller instantly; MockSimulatedDRO simulates "
         "movement over time with DRO/status callbacks.",
         ["Arduino", "Mock", "MockSimulatedDRO"]),
        ("baudrate", "int", "Serial baudrate used for the GRBL connection.", None),
        ("mock_linear_speed_mm_s", "float",
         "MockSimulatedDRO linear movement speed in mm/s.", None),
        ("mock_angular_speed_deg_s", "float",
         "MockSimulatedDRO angular movement speed in degrees/s.", None),
        ("mock_status_hz", "float",
         "MockSimulatedDRO DRO/status callback rate in Hz.", None),
    ],
    "windows": [
        ("port", "choice",
         "Windows COM port used when the GRBL streamer type is Arduino.",
         None),
    ],
    "logging": [
        ("level", "choice",
         "Terminal and file log verbosity. TRACE tells you almost everything; WARNING or ERROR keeps the terminal quiet.",
         ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"]),
        ("rotation", "str", "Log rotation policy (e.g. '100 MB' or '1 day').", None),
        ("retention", "str", "How long old logs are retained (e.g. '1 week').", None),
    ],
    "app": [
        ("show_machine_coordinate_system", "bool",
         "Show a second orange DRO row using machine coordinates alongside the work coordinates.", None),
        ("coord_viewer_backend", "choice",
         "3D coordinate viewer used in the native Grid pane.",
         ["matplotlib", "pyvista"]),
        ("show_rehome_button", "bool",
         "Show the ReHome command button in the machine control pane.", None),
        ("show_height_offset_controls", "bool",
         "Show the height offset input and Set height offset command in the machine control pane.", None),
        ("auto_hide_left_menu", "bool",
         "Automatically hide the left-hand navigation menu after selecting a view.", None),
        ("default_project_dir", "str",
         "Default parent folder used to create new session folders.", None),
        ("use_alternative_motion_controls", "bool",
         "Use a compact alternative layout for machine motion controls.", None),
    ],
    "debug": [
        ("direct_progress_ui", "bool",
         "Use direct worker progress callbacks instead of backend-polled progress. Leave off for normal use.", None),
        ("in_app_log_viewer", "bool",
         "Enable the browser-based System Log viewer. Leave off during measurement stability testing.", None),
        ("serial_comms", "bool",
         "Log GRBL serial connection/probe traffic during startup. Enable temporarily when diagnosing intermittent controller detection.", None),
        ("deep_ui_diagnostics", "bool",
         "Verbose UI diagnostics for scanner/progress update diagnosis. Not used by the native PySide UI.", None),
    ],
}

CONFIG_EDITOR_HIDDEN_SECTIONS = {"sweep", "grbl_streamer", "windows"}
CONFIG_EDITOR_SECTION_KEYS = {
    "audio": {"mode"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _strip_inline_comment(value: str) -> str:
    if value is None:
        return ""
    for sep in ("#", ";"):
        idx = value.find(sep)
        if idx >= 0:
            value = value[:idx]
    return value.strip()


def _parse_bool(s: str) -> bool:
    return str(s).strip().lower() in ("1", "true", "yes", "on")


def _normalize_decimal_text(raw: str) -> str:
    return raw.replace(",", ".")


def _coerce(kind: str, raw: str):
    raw = (raw or "").strip()
    if kind in ("str", "choice"):
        return raw
    if kind == "bool":
        return _parse_bool(raw)
    if kind == "int":
        return int(float(_normalize_decimal_text(raw)))
    if kind == "float":
        return float(_normalize_decimal_text(raw))
    if kind in ("opt_float", "optional_float"):
        if raw == "" or raw.lower() == "none":
            return None
        return float(_normalize_decimal_text(raw))
    raise ValueError(f"Unknown kind: {kind}")


def _format_for_ini(kind: str, value) -> str:
    if kind == "bool":
        return "True" if value else "False"
    if kind in ("opt_float", "optional_float") and value is None:
        return "None"
    return str(value)



def _device_options(catalog: dict, capability: str) -> Dict[int, str]:
    channel_key = "input_channels" if capability == "input" else "output_channels"
    return {
        dev_id: f"ID {dev_id}: {info['name']} ({info['hostapi']})"
        for dev_id, info in catalog.items()
        if info.get(channel_key)
    }


def _channel_options(catalog: dict, dev_id, capability: str) -> List[int]:
    try:
        dev_id = int(dev_id)
    except (TypeError, ValueError):
        return []
    channel_key = "input_channels" if capability == "input" else "output_channels"
    return list(catalog.get(dev_id, {}).get(channel_key, []))


def _sample_rate_options(rates: List[int]) -> Dict[int, str]:
    return {
        rate: f"{rate} (recommended)" if rate == 48000 else str(rate)
        for rate in rates
    }


def _serial_port_options(current: str = "") -> Dict[str, str]:
    current = _strip_inline_comment(current)
    try:
        from serial.tools import list_ports
    except ImportError:
        return {current: f"{current} (configured)"} if current else {}

    ports = sorted(list_ports.comports(), key=lambda port: port.device)
    options = {
        port.device: (
            port.device
            if not getattr(port, "description", "")
            else f"{port.device}: {port.description}"
        )
        for port in ports
    }
    if current and current not in options:
        options = {current: f"{current} (configured, not currently detected)", **options}
    return options



def save_config_values(
    config_file: str,
    values: Dict[Tuple[str, str], object],
    on_apply: Callable[[], None] | None = None,
) -> None:
    """Save selected config values through the same path as the config editor."""
    path = Path(config_file)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.optionxform = str  # type: ignore[assignment]
    parser.read(path)

    schema_lookup: Dict[Tuple[str, str], SchemaEntry] = {
        (section, e[0]): e
        for section, entries in EDITABLE_SCHEMA.items()
        for e in entries
    }

    for (section, key), raw_value in values.items():
        entry = schema_lookup.get((section, key))
        if entry is None:
            continue
        kind = entry[1]
        typed = _coerce(kind, "" if raw_value is None else str(raw_value))
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, key, _format_for_ini(kind, typed))

    backup_path = path.with_suffix(".old")
    try:
        import shutil
        shutil.copy2(path, backup_path)
        with open(path, "w", encoding="utf-8") as f:
            parser.write(f)
    except OSError as exc:
        logger.error(f"Failed to backup or write config: {exc}")
        raise

    logger.info(f"Configuration values saved to {path}; rebuilding dependent objects.")
    if on_apply is not None:
        on_apply()


