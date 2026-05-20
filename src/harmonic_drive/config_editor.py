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
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from loguru import logger
from nicegui import ui

from nfs.audio import get_devices_and_channels


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# Each entry: (key, kind, tooltip, options_or_none)
# kind ∈ {"str", "int", "float", "bool", "choice", "opt_float"}
SchemaEntry = Tuple[str, str, str, Optional[List[str]]]

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
        ("safe_radius", "float",
         "Minimum safe radius (mm) the motion manager retracts to before slewing.", None),
    ],
    "SphericalMeasurementMotionManager": [
        # No extra parameters beyond the base ones.
    ],
}

MEASUREMENT_POINTS_TYPES: Dict[str, List[SchemaEntry]] = {
    "FileMeasurementPoints": [
        ("filename", "str",
         "CSV file containing the measurement grid points.", None),
        ("homing_gap", "float", "Extra gap (mm) added at the homing position.", None),
        ("pole_gap", "float", "Extra gap (mm) added at the pole position.", None),
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
        ("homing_gap", "float", "Extra gap (mm) added at the homing position.", None),
        ("pole_gap", "float", "Extra gap (mm) added at the pole position.", None),
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
         "Input channel for the measurement microphone (0-based for ASIO, 1-based for WASAPI).", None),
        ("in_ch_loop", "int", "Input channel used as electrical loopback / reference.", None),
        ("out_ch_spkr", "int", "Output channel driving the speaker under test.", None),
        ("out_ch_ref", "int",
         "Output channel routed back into the loopback input for timing reference.", None),
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
         "If True, save raw loopback / reference / mic to Recordings/debug.", None),
        ("h2_test_db", "opt_float",
         "Inject 2nd harmonic at this dB level for distortion tests. 'None' to disable.", None),
        ("h3_test_db", "opt_float",
         "Inject 3rd harmonic at this dB level for distortion tests. 'None' to disable.", None),
    ],
    "scanner": [
        ("feed_rate", "int",
         "GRBL feed rate (mm/min) used for moves between measurement points.", None),
    ],
    "logging": [
        ("level", "choice",
         "Logger verbosity. TRACE is the most verbose, ERROR the least.",
         ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"]),
        ("rotation", "str", "Log rotation policy (e.g. '100 MB' or '1 day').", None),
        ("retention", "str", "How long old logs are retained (e.g. '1 week').", None),
    ],
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


def _coerce(kind: str, raw: str):
    raw = (raw or "").strip()
    if kind in ("str", "choice"):
        return raw
    if kind == "bool":
        return _parse_bool(raw)
    if kind == "int":
        return int(float(raw))
    if kind == "float":
        return float(raw)
    if kind == "opt_float":
        if raw == "" or raw.lower() == "none":
            return None
        return float(raw)
    raise ValueError(f"Unknown kind: {kind}")


def _format_for_ini(kind: str, value) -> str:
    if kind == "bool":
        return "True" if value else "False"
    if kind == "opt_float" and value is None:
        return "None"
    return str(value)


def _build_input(key: str, kind: str, current: str, options: Optional[List[str]]):
    label = key
    if kind == "bool":
        return ui.switch(label, value=_parse_bool(current))
    if kind == "choice":
        opts = list(options or [])
        if current and current not in opts:
            opts = [*opts, current]
        return ui.select(opts, value=current or (opts[0] if opts else ""), label=label) \
            .classes("w-full").props("outlined dense")
    return ui.input(label=label, value=current).classes("w-full").props("outlined dense")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
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
                if parser.has_section(section):
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
                                for key, kind, tooltip, options in EDITABLE_SCHEMA[section]:
                                    if not parser.has_option(section, key):
                                        continue
                                    raw = _strip_inline_comment(parser.get(section, key))
                                    el = _build_input(key, kind, raw, options)
                                    el.tooltip(tooltip)
                                    static_inputs[(section, key)] = el
                                if section == "audio":
                                    ui.button("List Sound Devices", on_click=_show_sound_devices) \
                                        .props("outline").classes("mt-4")

                # motion_manager (with inlined measurement-points subsection)
                if "motion_manager" in tab_objects:
                    with ui.tab_panel(tab_objects["motion_manager"]):
                        _build_motion_manager_panel(parser, dyn)

        with ui.row().classes("w-full justify-end mt-4 gap-2"):
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
