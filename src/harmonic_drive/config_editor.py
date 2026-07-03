"""
GUI-driven editor for the NFS / HarmonicDrive config.ini.

Opens a NiceGUI dialog populated from the on-disk config file. The user
can edit values per [section]; tooltips describe what each parameter
does. On *Cancel* the dialog is dismissed and nothing is changed; on
*OK* the values are written back to the file and a user-supplied
`on_apply` callback is invoked so that dependent objects can be
rebuilt.

The motion-manager section is *type-driven*: it has a fixed enum of
the two motion-manager classes and inlines the configuration of the
referenced measurement-points section, including a ``measurement_points``
type selector that dynamically rebuilds the associated fields below it.
The fields shown always match the constructor parameters of the chosen
motion-manager / measurement-points implementations.
"""
from __future__ import annotations

import configparser
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from loguru import logger
from nicegui import run, ui

from nfs.audio import (
    find_device_id_by_name,
    get_devices_and_channels,
    get_supported_sample_rates,
)


DEFAULT_CONFIG_FILENAME = "default_config.ini"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# Each entry: (key, kind, tooltip, options_or_none)
# kind ∈ {"str", "int", "float", "bool", "choice", "opt_float"}
SchemaEntry = Tuple[str, str, str, Optional[List[str]]]

DISPLAY_LABELS = {
    "level": "Terminal verbosity",
    "show_machine_coordinate_system": "Show machine coordinates",
    "show_rehome_button": "Show ReHome button",
    "show_height_offset_controls": "Show height offset controls",
    "default_project_dir": "Default session folder",
    "cal_tool_height": "Calibration tool height (mm)",
    "safe_radius": "Safe radius (mm)",
    "homing_gap": "Homing gap (degrees)",
    "pole_gap": "Pole gap (mm)",
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
         "NiceGUI/browser UI diagnostics for scanner/progress update diagnosis. Not used by the native PySide UI.", None),
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


def _build_input(key: str, kind: str, current: str, options: Optional[List[str]]):
    label = DISPLAY_LABELS.get(key, key.replace("_", " ").title())
    if kind == "bool":
        return ui.switch(label, value=_parse_bool(current))
    if kind == "choice":
        opts = dict(options) if isinstance(options, dict) else list(options or [])
        if isinstance(opts, dict):
            if current and current not in opts:
                opts = {current: current, **opts}
            value = current or (next(iter(opts)) if opts else "")
        else:
            if current and current not in opts:
                opts = [*opts, current]
            value = current or (opts[0] if opts else "")
        return ui.select(opts, value=value, label=label) \
            .classes("w-full").props("outlined dense")
    if key == "default_project_dir":
        with ui.row().classes("w-full items-end gap-2"):
            el = ui.input(label=label, value=current).classes("flex-1").props("outlined dense")
            ui.button(
                "Browse",
                icon="folder_open",
                on_click=lambda: _browse_folder_input(el),
            ).props("color=primary dense")
        return el
    return ui.input(label=label, value=current).classes("w-full").props("outlined dense")


def _native_folder_picker(initial_dir: str, title: str = "Select Default Session Folder") -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(
            title=title,
            initialdir=initial_dir,
            mustexist=False,
        )
    finally:
        root.destroy()


async def _browse_folder_input(input_el) -> None:
    initial_dir = str(Path(input_el.value or Path.home()).expanduser().resolve())
    try:
        selected = await run.io_bound(_native_folder_picker, initial_dir)
    except Exception as exc:
        ui.notify(f"Could not open folder browser: {exc}", type="negative")
        return
    if selected:
        input_el.set_value(selected)


def _default_config_path(path: Path) -> Path:
    return path.with_name(DEFAULT_CONFIG_FILENAME)


def restore_default_config(config_file: str | Path, on_apply: Callable[[], None] | None = None) -> bool:
    path = Path(config_file)
    default_path = _default_config_path(path)
    if not default_path.exists():
        ui.notify(f"Default config not found: {default_path}", type="negative")
        return False

    backup_path = path.with_suffix(".old")
    try:
        if path.exists():
            shutil.copy2(path, backup_path)
        shutil.copy2(default_path, path)
    except OSError as exc:
        logger.error(f"Failed to restore default config: {exc}")
        ui.notify(f"Failed to restore defaults: {exc}", type="negative")
        return False

    logger.info(f"Restored default config from {default_path}")
    if on_apply is not None:
        on_apply()
    ui.notify("Default configuration restored", type="positive")
    return True


class _ConfigValueRef:
    """Tiny value holder so derived config values can be saved like UI elements."""

    def __init__(self, value=""):
        self.value = value


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


def _set_select_options(select, options, value=None) -> None:
    select.options = options
    if value is not None:
        select.value = value
    select.update()


def _build_audio_panel(parser, static_inputs: Dict[Tuple[str, str], object]) -> None:
    """Render audio settings with device/channel selectors backed by sounddevice."""
    section = "audio"
    catalog = get_devices_and_channels()
    refs = {
        "in_dev_name": _ConfigValueRef(_strip_inline_comment(parser.get(section, "in_dev_name", fallback=""))),
        "in_dev_hostapi": _ConfigValueRef(_strip_inline_comment(parser.get(section, "in_dev_hostapi", fallback=""))),
        "out_dev_name": _ConfigValueRef(_strip_inline_comment(parser.get(section, "out_dev_name", fallback=""))),
        "out_dev_hostapi": _ConfigValueRef(_strip_inline_comment(parser.get(section, "out_dev_hostapi", fallback=""))),
    }

    for key, ref in refs.items():
        static_inputs[(section, key)] = ref

    def current_int(key: str, fallback: int = 0) -> int:
        raw = _strip_inline_comment(parser.get(section, key, fallback=str(fallback)))
        try:
            return int(float(raw))
        except ValueError:
            return fallback

    mode_entry = next(e for e in EDITABLE_SCHEMA[section] if e[0] == "mode")
    mode_raw = _strip_inline_comment(parser.get(section, "mode", fallback="hardware"))
    mode_el = _build_input("mode", mode_entry[1], mode_raw, mode_entry[3])
    mode_el.tooltip(mode_entry[2])
    static_inputs[(section, "mode")] = mode_el

    device_selects = {}
    channel_selects = {}
    fs_select = None

    def sync_device_metadata(role: str) -> None:
        select = device_selects.get(role)
        if select is None:
            return
        try:
            dev_id = int(select.value)
        except (TypeError, ValueError):
            dev_id = None
        info = catalog.get(dev_id, {}) if dev_id is not None else {}
        refs[f"{role}_dev_name"].value = info.get("name", "")
        refs[f"{role}_dev_hostapi"].value = info.get("hostapi", "")

    def refresh_channels(role: str) -> None:
        capability = "input" if role == "in" else "output"
        dev_select = device_selects.get(role)
        if dev_select is None:
            return
        opts = _channel_options(catalog, dev_select.value, capability)
        keys = ("ch_in_mic", "ch_in_loop") if role == "in" else ("ch_out_spkr", "ch_out_ref")
        for key in keys:
            ch_select = channel_selects.get(key)
            if ch_select is None:
                continue
            current = ch_select.value
            if current not in opts:
                current = opts[0] if opts else None
            _set_select_options(ch_select, opts, current)
        sync_device_metadata(role)

    def selected_device_id(role: str) -> Optional[int]:
        try:
            return int(device_selects[role].value)
        except (KeyError, TypeError, ValueError):
            return None

    def refresh_sample_rates() -> None:
        if fs_select is None:
            return
        current = fs_select.value
        rates = get_supported_sample_rates(
            selected_device_id("in"),
            selected_device_id("out"),
        )
        if not rates and current:
            rates = [int(current)]
        elif current not in rates and rates:
            current = rates[0]
        _set_select_options(fs_select, _sample_rate_options(rates), current)

    def refresh_devices() -> None:
        nonlocal catalog
        catalog = get_devices_and_channels()
        for role, capability in (("in", "input"), ("out", "output")):
            opts = _device_options(catalog, capability)
            select = device_selects.get(role)
            if select is None:
                continue
            current = select.value
            if current not in opts:
                current = next(iter(opts), None)
            _set_select_options(select, opts, current)
            refresh_channels(role)
        refresh_sample_rates()
        ui.notify("Audio device list refreshed", type="positive")

    with ui.row().classes("w-full gap-2"):
        in_options = _device_options(catalog, "input")
        out_options = _device_options(catalog, "output")
        in_value = current_int("in_dev")
        out_value = current_int("out_dev")
        if in_value not in in_options and in_options:
            in_value = next(iter(in_options))
        if out_value not in out_options and out_options:
            out_value = next(iter(out_options))

        in_select = ui.select(in_options, value=in_value, label="Input device") \
            .classes("w-full").props("outlined dense")
        out_select = ui.select(out_options, value=out_value, label="Output device") \
            .classes("w-full").props("outlined dense")
        device_selects["in"] = in_select
        device_selects["out"] = out_select
        static_inputs[(section, "in_dev")] = in_select
        static_inputs[(section, "out_dev")] = out_select

    with ui.row().classes("w-full gap-2"):
        for key, label in (
            ("in_ch_mic", "Mic input channel"),
            ("in_ch_loop", "Loopback input channel"),
        ):
            opts = _channel_options(catalog, in_select.value, "input")
            value = current_int(key)
            if value not in opts and opts:
                value = opts[0]
            el = ui.select(opts, value=value, label=label).classes("w-full").props("outlined dense")
            channel_selects[key] = el
            static_inputs[(section, key)] = el

    with ui.row().classes("w-full gap-2"):
        for key, label in (
            ("out_ch_spkr", "Speaker output channel"),
            ("out_ch_ref", "Loopback output channel"),
        ):
            opts = _channel_options(catalog, out_select.value, "output")
            value = current_int(key)
            if value not in opts and opts:
                value = opts[0]
            el = ui.select(opts, value=value, label=label).classes("w-full").props("outlined dense")
            channel_selects[key] = el
            static_inputs[(section, key)] = el

    for role in ("in", "out"):
        def on_device_change(_e, r=role):
            refresh_channels(r)
            refresh_sample_rates()

        device_selects[role].on("update:model-value", on_device_change)
        sync_device_metadata(role)

    fs_entry = next(e for e in EDITABLE_SCHEMA[section] if e[0] == "fs")
    fs_value = current_int("fs", 48000)
    fs_options = get_supported_sample_rates(selected_device_id("in"), selected_device_id("out"))
    if not fs_options:
        fs_options = [fs_value]
    elif fs_value not in fs_options:
        fs_value = fs_options[0]
    fs_select = ui.select(_sample_rate_options(fs_options), value=fs_value, label="fs") \
        .classes("w-full").props("outlined dense")
    fs_select.tooltip(fs_entry[2])
    static_inputs[(section, "fs")] = fs_select

    skip_keys = {
        "mode", "in_dev", "out_dev", "in_ch_mic", "in_ch_loop", "out_ch_spkr",
        "out_ch_ref", "in_dev_name", "in_dev_hostapi", "out_dev_name", "out_dev_hostapi", "fs",
    }
    for key, kind, tooltip, options in EDITABLE_SCHEMA[section]:
        if key in skip_keys:
            continue
        if not parser.has_option(section, key):
            continue
        raw = _strip_inline_comment(parser.get(section, key))
        el = _build_input(key, kind, raw, options)
        el.tooltip(tooltip)
        static_inputs[(section, key)] = el

    ui.button("Refresh Sound Devices", icon="refresh", on_click=refresh_devices).props("outline").classes("mt-4")
    _prompt_for_audio_device_relink(parser, catalog, device_selects, refresh_channels, refresh_sample_rates)


def _build_scanner_panel(parser, static_inputs: Dict[Tuple[str, str], object]) -> None:
    """Render scanner settings plus GRBL connection settings."""
    ui.label("Scanner").classes("text-base font-semibold")
    for key, kind, tooltip, options in EDITABLE_SCHEMA["scanner"]:
        if not parser.has_option("scanner", key):
            if key == "cal_tool_height":
                raw = "0.0"
            else:
                continue
        else:
            raw = _strip_inline_comment(parser.get("scanner", key))
        el = _build_input(key, kind, raw, options)
        el.tooltip(tooltip)
        static_inputs[("scanner", key)] = el

    ui.separator()
    ui.label("GRBL connection").classes("text-base font-semibold")
    grbl_type_input = None
    mock_dro_inputs = []
    for section, key, label in (
        ("grbl_streamer", "type", "GRBL streamer type"),
        ("grbl_streamer", "baudrate", "Baudrate"),
        ("grbl_streamer", "mock_linear_speed_mm_s", "Mock linear speed mm/s"),
        ("grbl_streamer", "mock_angular_speed_deg_s", "Mock angular speed deg/s"),
        ("grbl_streamer", "mock_status_hz", "Mock DRO update Hz"),
        ("windows", "port", "COM port"),
    ):
        if not parser.has_section(section):
            continue
        entry = next(e for e in EDITABLE_SCHEMA[section] if e[0] == key)
        _key, kind, tooltip, options = entry
        fallback = ""
        if section == "grbl_streamer":
            fallback = {
                "mock_linear_speed_mm_s": "500.0",
                "mock_angular_speed_deg_s": "180.0",
                "mock_status_hz": "5.0",
            }.get(key, "")
        raw = _strip_inline_comment(parser.get(section, key, fallback=fallback))
        if section == "windows" and key == "port":
            options = _serial_port_options(raw)
        el = _build_input(label, kind, raw, options)
        el.tooltip(tooltip)
        static_inputs[(section, key)] = el
        if section == "grbl_streamer" and key == "type":
            grbl_type_input = el
        elif key in {
            "mock_linear_speed_mm_s",
            "mock_angular_speed_deg_s",
            "mock_status_hz",
        }:
            mock_dro_inputs.append(el)

    def update_mock_dro_visibility() -> None:
        streamer_type = getattr(grbl_type_input, "value", "")
        visible = streamer_type in {"MockSimulatedDRO", "MockWithDRO"}
        for el in mock_dro_inputs:
            el.set_visibility(visible)

    if grbl_type_input is not None:
        grbl_type_input.on("update:model-value", lambda _e: update_mock_dro_visibility())
        update_mock_dro_visibility()


def _prompt_for_audio_device_relink(
    parser,
    catalog: dict,
    device_selects: dict,
    refresh_channels,
    refresh_sample_rates,
) -> None:
    prompts = []
    for role, capability in (("in", "input"), ("out", "output")):
        id_key = f"{role}_dev"
        name_key = f"{role}_dev_name"
        api_key = f"{role}_dev_hostapi"
        saved_name = _strip_inline_comment(parser.get("audio", name_key, fallback=""))
        saved_api = _strip_inline_comment(parser.get("audio", api_key, fallback=""))
        if not saved_name:
            continue
        try:
            old_id = int(float(_strip_inline_comment(parser.get("audio", id_key, fallback="-1"))))
        except ValueError:
            old_id = -1
        old_info = catalog.get(old_id)
        if old_info and old_info.get("name") == saved_name and (
            not saved_api or old_info.get("hostapi") == saved_api
        ):
            continue
        new_id = find_device_id_by_name(
            saved_name,
            saved_api,
            require_input=capability == "input",
            require_output=capability == "output",
        )
        if new_id is not None and new_id != old_id:
            prompts.append((role, saved_name, old_id, new_id))

    for role, saved_name, old_id, new_id in prompts:
        role_label = "input" if role == "in" else "output"
        with ui.dialog() as prompt, ui.card().classes("w-[420px] max-w-full"):
            ui.label("Audio device ID changed").classes("text-lg font-bold")
            ui.label(
                f"The {role_label} device '{saved_name}' appears to have changed "
                f"from ID {old_id} to ID {new_id}. "
                "Use this device?"
            )
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("No", on_click=prompt.close).props("flat")

                def accept(p=prompt, r=role, d=new_id):
                    device_selects[r].value = d
                    device_selects[r].update()
                    refresh_channels(r)
                    refresh_sample_rates()
                    p.close()

                ui.button("Use Device", on_click=accept).props("color=primary")
        prompt.open()


def check_audio_device_ids_on_startup(
    config_file: str,
    on_apply: Callable[[], None] | None = None,
) -> None:
    """Prompt when saved audio device names now resolve to different IDs."""
    path = Path(config_file)
    if not path.exists():
        return

    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.optionxform = str  # type: ignore[assignment]
    parser.read(path)
    if not parser.has_section("audio"):
        return

    catalog = get_devices_and_channels()
    prompts = []
    for role, capability in (("in", "input"), ("out", "output")):
        id_key = f"{role}_dev"
        name_key = f"{role}_dev_name"
        api_key = f"{role}_dev_hostapi"
        saved_name = _strip_inline_comment(parser.get("audio", name_key, fallback=""))
        saved_api = _strip_inline_comment(parser.get("audio", api_key, fallback=""))
        if not saved_name:
            continue
        try:
            old_id = int(float(_strip_inline_comment(parser.get("audio", id_key, fallback="-1"))))
        except ValueError:
            old_id = -1

        old_info = catalog.get(old_id)
        if old_info and old_info.get("name") == saved_name and (
            not saved_api or old_info.get("hostapi") == saved_api
        ):
            continue

        new_id = find_device_id_by_name(
            saved_name,
            saved_api,
            require_input=capability == "input",
            require_output=capability == "output",
        )
        if new_id is not None and new_id != old_id:
            prompts.append((role, saved_name, old_id, new_id))

    def accept_device(prompt, role: str, new_id: int) -> None:
        parser.set("audio", f"{role}_dev", str(new_id))
        try:
            import shutil
            shutil.copy2(path, path.with_suffix(".old"))
            with open(path, "w") as f:
                parser.write(f)
        except OSError as exc:
            logger.error(f"Failed to update audio device ID: {exc}")
            ui.notify(f"Failed to update audio device ID: {exc}", type="negative")
            return

        prompt.close()
        ui.notify("Audio device ID updated", type="positive")
        if on_apply is not None:
            on_apply()

    for role, saved_name, old_id, new_id in prompts:
        role_label = "input" if role == "in" else "output"
        with ui.dialog() as prompt, ui.card().classes("w-[420px] max-w-full"):
            ui.label("Audio device ID changed").classes("text-lg font-bold")
            ui.label(
                f"The {role_label} device '{saved_name}' appears to have changed "
                f"from ID {old_id} to ID {new_id}. "
                "Use this device?"
            )
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("No", on_click=prompt.close).props("flat")
                ui.button(
                    "Use Device",
                    on_click=lambda p=prompt, r=role, d=new_id: accept_device(p, r, d),
                ).props("color=primary")
        prompt.open()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def set_file_measurement_points_filename(
    config_file: str,
    filename: str,
    on_apply: Callable[[], None] | None = None,
) -> str:
    """
    Point the configured FileMeasurementPoints source at ``filename``.

    This is the non-dialog counterpart to saving the measurement-points filename
    through the config editor. It preserves whether the config uses inline
    measurement-points settings or a referenced measurement-points section.

    :return: The section updated with the filename.
    """
    path = Path(config_file)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.optionxform = str  # type: ignore[assignment]
    parser.read(path)

    if not parser.has_section("motion_manager"):
        raise KeyError("Config file missing [motion_manager] section")

    mp_section = _strip_inline_comment(
        parser.get("motion_manager", "measurement_points", fallback="")
    )
    target_section = mp_section or "motion_manager"
    if mp_section and not parser.has_section(mp_section):
        parser.add_section(mp_section)

    if mp_section:
        parser.set(mp_section, "type", "FileMeasurementPoints")
        parser.set(mp_section, "filename", filename)
    else:
        parser.set("motion_manager", "measurement_points_type", "FileMeasurementPoints")
        parser.set("motion_manager", "filename", filename)

    backup_path = path.with_suffix(".old")
    try:
        import shutil
        shutil.copy2(path, backup_path)
        with open(path, "w") as f:
            parser.write(f)
    except OSError as exc:
        logger.error(f"Failed to backup or write config: {exc}")
        raise

    logger.info(f"Updated [{target_section}] filename to {filename}; reloading config.")
    if on_apply is not None:
        on_apply()
    return target_section


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


def open_config_editor(config_file: str, on_apply: Callable[[], None]) -> None:
    path = Path(config_file)
    if not path.exists():
        ui.notify(f"Config file not found: {path}", type="negative")
        return

    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.optionxform = str  # type: ignore[assignment]
    parser.read(path)

    # Inputs for static sections: (section, key) -> element
    static_inputs: Dict[Tuple[str, str], object] = {}

    # Dynamic state for the combined motion_manager / measurement-points panel.
    dyn: Dict[str, object] = {
        "mm_type_select": None,
        "mp_type_select": None,
        "mm_fields_container": None,
        "mp_fields_container": None,
        "mm_inputs": {},   # key -> (element, kind), motion_manager extra fields
        "mp_inputs": {},   # key -> (element, kind), measurement-points fields
        "mp_section_name": None,  # name of the [section] referenced by motion_manager
    }

    # `transition-show/hide=none` disables the default slide-in animation.
    dialog = ui.dialog().props('persistent transition-show="none" transition-hide="none"')
    # Responsive sizing: ~half screen on desktop (min 600px), full screen on mobile.
    # Fixed height with internal scrolling prevents the dialog from resizing
    # when switching tabs whose content has different heights.
    with dialog, ui.card().classes(
        "w-[50vw] min-w-[600px] max-w-full h-[80vh] "
        "max-sm:w-screen max-sm:min-w-0 max-sm:h-screen max-sm:max-w-none "
        "flex flex-col"
    ):
        ui.label("Edit configuration").classes("text-xl font-bold")
        ui.label(str(path)).classes("text-xs text-gray-500 mb-2")

        tab_order: List[str] = []
        tab_objects: Dict[str, object] = {}

        with ui.tabs().classes("w-full") as tabs:
            for section in EDITABLE_SCHEMA:
                if parser.has_section(section) and section not in CONFIG_EDITOR_HIDDEN_SECTIONS:
                    tab_objects[section] = ui.tab(section)
                    tab_order.append(section)
            if parser.has_section("motion_manager"):
                tab_objects["motion_manager"] = ui.tab("motion_manager")
                tab_order.append("motion_manager")
                # Remember which section motion_manager currently references; the
                # GUI inlines its keys into the motion_manager tab and writes them
                # back under that section name on OK.
                mp_section = _strip_inline_comment(
                    parser.get("motion_manager", "measurement_points", fallback="")
                )
                if mp_section:
                    dyn["mp_section_name"] = mp_section

        if not tab_objects:
            ui.label("No editable sections found in this config.")
        else:
            with ui.tab_panels(tabs, value=tab_objects[tab_order[0]]).classes(
                "w-full flex-1 overflow-auto"
            ):
                # Static sections
                for section in tab_order:
                    if section in EDITABLE_SCHEMA:
                        with ui.tab_panel(tab_objects[section]):
                            with ui.column().classes("w-full gap-2"):
                                if section == "audio":
                                    visible_keys = CONFIG_EDITOR_SECTION_KEYS.get(section)
                                    for key, kind, tooltip, options in EDITABLE_SCHEMA[section]:
                                        if visible_keys is not None and key not in visible_keys:
                                            continue
                                        if not parser.has_option(section, key):
                                            continue
                                        raw = _strip_inline_comment(parser.get(section, key))
                                        el = _build_input(key, kind, raw, options)
                                        el.tooltip(tooltip)
                                        static_inputs[(section, key)] = el
                                elif section == "scanner":
                                    _build_scanner_panel(parser, static_inputs)
                                else:
                                    for key, kind, tooltip, options in EDITABLE_SCHEMA[section]:
                                        if not parser.has_option(section, key):
                                            if (
                                                section == "app"
                                                and key in {
                                                    "show_machine_coordinate_system",
                                                    "show_rehome_button",
                                                    "show_height_offset_controls",
                                                "use_alternative_motion_controls",
                                                }
                                            ):
                                                raw = (
                                                    "True"
                                                    if key in {
                                                        "show_rehome_button",
                                                        "show_height_offset_controls",
                                                    }
                                                    else "False"
                                                )
                                            else:
                                                continue
                                        else:
                                            raw = _strip_inline_comment(parser.get(section, key))
                                        el = _build_input(key, kind, raw, options)
                                        el.tooltip(tooltip)
                                        static_inputs[(section, key)] = el

                # motion_manager (with inlined measurement-points subsection)
                if "motion_manager" in tab_objects:
                    with ui.tab_panel(tab_objects["motion_manager"]):
                        _build_motion_manager_panel(parser, dyn)

        def confirm_restore_defaults():
            default_path = _default_config_path(path)
            if not default_path.exists():
                ui.notify(f"Default config not found: {default_path}", type="negative")
                return

            with ui.dialog() as confirm, ui.card().classes("w-[420px] max-w-full"):
                ui.label("Restore default settings?").classes("text-lg font-bold")
                ui.label(
                    "This will replace the current config.ini with default_config.ini "
                    "and save a .old backup."
                ).classes("text-sm text-gray-700")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Cancel", on_click=confirm.close).props("flat")

                    def do_restore():
                        if restore_default_config(path, on_apply):
                            confirm.close()
                            dialog.close()

                    ui.button("Restore Defaults", on_click=do_restore).props("color=negative")
            confirm.open()

        with ui.row().classes("w-full justify-between mt-4 gap-2"):
            ui.button(
                "Restore Defaults",
                icon="restart_alt",
                on_click=confirm_restore_defaults,
            ).props("outline color=negative")
            with ui.row().classes("gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "OK",
                    on_click=lambda: _on_ok(parser, path, static_inputs, dyn, dialog, on_apply),
                ).props("color=primary")

    dialog.open()


def _show_sound_devices() -> None:
    """Show a list of available sound devices in a separate window."""
    catalog = get_devices_and_channels()
    # Note: 'seamless' allows interacting with the background.
    # 'position=right' keeps it from centering over the main editor.
    with ui.dialog().props('seamless position=right transition-show="none" transition-hide="none"') as d:
        with ui.card().classes("w-[40vw] min-w-[400px] h-[60vh] flex flex-col shadow-2xl").style("resize: both; overflow: auto;"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Available Audio Devices").classes("text-lg font-bold")
                ui.button(icon="close", on_click=d.close).props("flat round dense")
            
            with ui.scroll_area().classes("flex-1"):
                for dev_id, info in catalog.items():
                    with ui.column().classes("mb-4 gap-0"):
                        # 'select-text' allows user to highlight and copy
                        ui.label(f"ID {dev_id}: {info['name']}").classes("font-bold select-text")
                        ui.label(f"  API: {info['hostapi']}").classes("text-xs text-gray-600 select-text")
                        if info['input_channels']:
                            ui.label(f"  In Channels: {info['input_channels']}").classes("text-xs select-text")
                        if info['output_channels']:
                            ui.label(f"  Out Channels: {info['output_channels']}").classes("text-xs select-text")
    d.open()


def _build_motion_manager_panel(parser, dyn) -> None:
    """Render the motion_manager tab.

    Layout:
      - motion-manager `type` selector (fixed enum of the two factory classes)
      - dynamic motion-manager fields (e.g. ``safe_radius``)
      - inlined measurement-points block (section name + `type` + dynamic fields)
    """
    mm_available_types = list(MOTION_MANAGER_TYPES.keys())
    current_mm_type = _strip_inline_comment(
        parser.get("motion_manager", "type", fallback="")
    )
    if current_mm_type and current_mm_type not in mm_available_types:
        # Preserve unknown type so existing config isn't lost, but warn the user.
        mm_available_types = [*mm_available_types, current_mm_type]

    mp_section_name = dyn.get("mp_section_name") or ""
    current_mp_type = ""
    # Try getting type from referenced section, or from motion_manager directly (inline)
    if mp_section_name and parser.has_section(mp_section_name):
        current_mp_type = _strip_inline_comment(
            parser.get(mp_section_name, "type", fallback="")
        )
    else:
        current_mp_type = _strip_inline_comment(
            parser.get("motion_manager", "measurement_points_type", fallback="")
        )
        if not current_mp_type:
            current_mp_type = _strip_inline_comment(
                parser.get("motion_manager", "type", fallback="")
            )
            # Only use it if it's one of the known MP types
            if current_mp_type not in MEASUREMENT_POINTS_TYPES:
                current_mp_type = ""
    mp_available_types = list(MEASUREMENT_POINTS_TYPES.keys())
    if current_mp_type and current_mp_type not in mp_available_types:
        mp_available_types = [*mp_available_types, current_mp_type]

    with ui.column().classes("w-full gap-3"):
        # ----- Motion manager block -------------------------------------------------
        ui.label("Motion manager").classes("text-base font-semibold")
        mm_type_select = ui.select(
            mm_available_types, value=current_mm_type or mm_available_types[0],
            label="type",
        ).classes("w-full").props("outlined dense")
        mm_type_select.tooltip(
            "Motion manager class. Changing this updates the fields below."
        )
        dyn["mm_type_select"] = mm_type_select

        mm_fields_container = ui.column().classes("w-full gap-2")
        dyn["mm_fields_container"] = mm_fields_container

        def _rebuild_mm_fields():
            mm_fields_container.clear()
            dyn["mm_inputs"] = {}
            t = mm_type_select.value
            entries = MOTION_MANAGER_TYPES.get(t, [])
            with mm_fields_container:
                for key, kind, tooltip, options in entries:
                    raw = _strip_inline_comment(
                        parser.get("motion_manager", key, fallback="")
                    )
                    el = _build_input(key, kind, raw, options)
                    el.tooltip(tooltip)
                    dyn["mm_inputs"][key] = (el, kind)

        mm_type_select.on("update:model-value", lambda _e: _rebuild_mm_fields())
        _rebuild_mm_fields()

        ui.separator()

        # ----- Measurement-points block (inlined) ----------------------------------
        ui.label("Measurement points").classes("text-base font-semibold")

        section_input = ui.input(
            label="measurement_points section (leave empty to inline)",
            value=mp_section_name,
        ).classes("w-full").props("outlined dense")
        section_input.tooltip(
            "Name of the [section] used for the measurement-points settings. "
            "If empty, settings are saved directly in [motion_manager]."
        )
        dyn["mp_section_input"] = section_input

        mp_type_select = ui.select(
            mp_available_types,
            value=current_mp_type or (mp_available_types[0] if mp_available_types else ""),
            label="type",
        ).classes("w-full").props("outlined dense")
        mp_type_select.tooltip(
            "MeasurementPoints class. Changing this updates the fields below."
        )
        dyn["mp_type_select"] = mp_type_select

        mp_fields_container = ui.column().classes("w-full gap-2")
        dyn["mp_fields_container"] = mp_fields_container

        def _rebuild_mp_fields(e=None):
            # If e is passed, it means it's from section_input's update.
            # We want to use the fresh value from the event if possible.
            new_section = e.value if (e and hasattr(e, 'value')) else section_input.value
            mp_fields_container.clear()
            dyn["mp_inputs"] = {}
            t = mp_type_select.value
            entries = MEASUREMENT_POINTS_TYPES.get(t, [])
            section_for_lookup = new_section or "motion_manager"
            
            with mp_fields_container:
                if not entries and t:
                    ui.label(
                        f"No schema known for type '{t}'. Existing keys are preserved as-is."
                    ).classes("text-xs text-gray-500")
                for key, kind, tooltip, options in entries:
                    raw = ""
                    if section_for_lookup and parser.has_section(section_for_lookup):
                        raw = _strip_inline_comment(
                            parser.get(section_for_lookup, key, fallback="")
                        )
                    el = _build_input(key, kind, raw, options)
                    el.tooltip(tooltip)
                    dyn["mp_inputs"][key] = (el, kind)

        section_input.on("update:model-value", _rebuild_mp_fields)
        mp_type_select.on("update:model-value", _rebuild_mp_fields)
        _rebuild_mp_fields()


def _on_ok(parser, path, static_inputs, dyn, dialog, on_apply: Callable[[], None]) -> None:
    new_values: Dict[Tuple[str, str], str] = {}

    # Static sections
    schema_lookup: Dict[Tuple[str, str], SchemaEntry] = {
        (section, e[0]): e for section, entries in EDITABLE_SCHEMA.items() for e in entries
    }
    for (section, key), el in static_inputs.items():
        kind = schema_lookup[(section, key)][1]
        raw = getattr(el, "value", "")
        try:
            typed = _coerce(kind, "" if raw is None else str(raw))
        except Exception as exc:
            ui.notify(f"[{section}] {key}: invalid value ({exc})", type="negative")
            return
        if kind == "optional_float" and typed is None:
            if parser.has_option(section, key):
                parser.remove_option(section, key)
            continue
        new_values[(section, key)] = _format_for_ini(kind, typed)

    # motion_manager + inlined measurement-points
    mm_type_sel = dyn.get("mm_type_select")
    if mm_type_sel is not None:
        mm_type = (mm_type_sel.value or "").strip()
        if not mm_type:
            ui.notify("[motion_manager] type must be set", type="negative")
            return

        section_input = dyn.get("mp_section_input")
        new_mp_section = (section_input.value or "").strip() if section_input else ""

        new_values[("motion_manager", "type")] = mm_type
        if new_mp_section:
            new_values[("motion_manager", "measurement_points")] = new_mp_section
        else:
            # If inlining, ensure 'measurement_points' key is removed
            if parser.has_option("motion_manager", "measurement_points"):
                parser.remove_option("motion_manager", "measurement_points")

        for key, (el, kind) in dyn["mm_inputs"].items():
            raw = getattr(el, "value", "")
            try:
                typed = _coerce(kind, "" if raw is None else str(raw))
            except Exception as exc:
                ui.notify(f"[motion_manager] {key}: invalid value ({exc})", type="negative")
                return
            if kind == "optional_float" and typed is None:
                if parser.has_option("motion_manager", key):
                    parser.remove_option("motion_manager", key)
                continue
            new_values[("motion_manager", key)] = _format_for_ini(kind, typed)

        # Remove now-stale extra keys belonging to *other* motion-manager types.
        allowed_mm_keys = {"type", "measurement_points"} | {
            e[0] for e in MOTION_MANAGER_TYPES.get(mm_type, [])
        }
        all_known_mm_keys = {"type", "measurement_points"} | {
            e[0] for entries in MOTION_MANAGER_TYPES.values() for e in entries
        }
        for stale_key in all_known_mm_keys - allowed_mm_keys:
            if parser.has_option("motion_manager", stale_key):
                parser.remove_option("motion_manager", stale_key)

        # ----- Measurement-points section -----
        mp_type_sel = dyn.get("mp_type_select")
        old_mp_section = dyn.get("mp_section_name") or ""
        if mp_type_sel is not None:
            mp_type = (mp_type_sel.value or "").strip()
            if not mp_type:
                ui.notify(f"[{new_mp_section}] type must be set", type="negative")
                return

            # If the user renamed the section, drop the old one to keep INI clean.
            if old_mp_section and old_mp_section != new_mp_section \
                    and parser.has_section(old_mp_section):
                parser.remove_section(old_mp_section)

            target_section = new_mp_section or "motion_manager"
            if new_mp_section and not parser.has_section(new_mp_section):
                parser.add_section(new_mp_section)

            if new_mp_section:
                new_values[(new_mp_section, "type")] = mp_type
            else:
                # Use the alias to avoid collision with motion_manager's 'type'
                new_values[("motion_manager", "measurement_points_type")] = mp_type
                # Also ensure 'type' isn't overwritten by mistake if it was in mp_inputs
                if ("motion_manager", "type") in new_values and mp_type != new_values[("motion_manager", "type")]:
                     # This is expected, motion_manager type is different from mp type
                     pass

            for key, (el, kind) in dyn["mp_inputs"].items():
                raw = getattr(el, "value", "")
                try:
                    typed = _coerce(kind, "" if raw is None else str(raw))
                except Exception as exc:
                    ui.notify(f"[{target_section}] {key}: invalid value ({exc})",
                              type="negative")
                    return
                if kind == "optional_float" and typed is None:
                    if parser.has_option(target_section, key):
                        parser.remove_option(target_section, key)
                    continue
                new_values[(target_section, key)] = _format_for_ini(kind, typed)

            # Remove stale keys (from previously selected mp types) under target section.
            allowed_mp_keys = {"type", "measurement_points_type"} | {
                e[0] for e in MEASUREMENT_POINTS_TYPES.get(mp_type, [])
            }
            all_known_mp_keys = {"type", "measurement_points_type"} | {
                e[0] for entries in MEASUREMENT_POINTS_TYPES.values() for e in entries
            }
            for stale_key in all_known_mp_keys - allowed_mp_keys:
                if parser.has_option(target_section, stale_key):
                    # DO NOT remove 'type' or 'safe_radius' if target is motion_manager
                    # Also don't remove measurement_points_type if it is allowed
                    if target_section == "motion_manager" and stale_key in {"type", "safe_radius", "measurement_points", "measurement_points_type"}:
                        continue
                    parser.remove_option(target_section, stale_key)

    # Apply to parser. Ensure sections exist and create them if needed.
    for (section, key), formatted in new_values.items():
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, key, formatted)

    save_path = path
    backup_path = path.with_suffix(".old")
    try:
        if path.exists():
            import shutil
            shutil.copy2(path, backup_path)
        with open(save_path, "w") as f:
            parser.write(f)
    except OSError as exc:
        logger.error(f"Failed to backup or write config: {exc}")
        ui.notify(f"Failed to backup or write config: {exc}", type="negative")
        return

    dialog.close()
    logger.info(f"Configuration saved to {save_path}; rebuilding dependent objects.")
    try:
        on_apply()
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("on_apply callback failed")
        ui.notify(f"Reload failed: {exc}", type="negative")
