from __future__ import annotations

import asyncio
import configparser
from typing import Any, Dict

from nicegui import ui
from nicegui.timer import Timer as BackgroundTimer

from harmonic_drive import control, project
from harmonic_drive.config_editor import (
    _channel_options,
    _device_options,
    _parse_bool,
    _sample_rate_options,
    _set_select_options,
    _strip_inline_comment,
    save_config_values,
)
from nfs.audio import get_audio_meter_state, get_devices_and_channels, get_supported_sample_rates


_cal_meter_timer: BackgroundTimer | None = None


def _read_config(config_file: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.optionxform = str  # type: ignore[assignment]
    parser.read(config_file)
    return parser


def _value(parser: configparser.ConfigParser, section: str, key: str, fallback: str = "") -> str:
    return _strip_inline_comment(parser.get(section, key, fallback=fallback))


def _int_value(parser, section: str, key: str, fallback: int = 0) -> int:
    try:
        return int(float(_value(parser, section, key, str(fallback))))
    except ValueError:
        return fallback


def _float_value(parser, section: str, key: str, fallback: float = 0.0) -> float:
    try:
        return float(_value(parser, section, key, str(fallback)))
    except ValueError:
        return fallback


def _current_a_weighted_mic_peak_dbfs() -> float | None:
    state = get_audio_meter_state()
    inputs = state.get("a_weighted_inputs", [])
    if len(inputs) < 2:
        return None
    peak = inputs[1].get("peak_dbfs")
    if peak is None:
        return None
    return float(peak)


def _format_dbfs(value: float | None) -> str:
    if value is None or value <= -119.0:
        return "-inf dBFS"
    return f"{value:.1f} dBFS"


def _optional_float_value(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_float_text(parser, section: str, key: str, fallback: str = "") -> str:
    raw = _value(parser, section, key, fallback)
    if raw.strip().lower() in ("", "none"):
        return ""
    return raw


def _section_dict(parser: configparser.ConfigParser, section: str) -> Dict[str, str]:
    if not parser.has_section(section):
        return {}
    return {key: _strip_inline_comment(value) for key, value in parser.items(section)}


def _audio_api_options(catalog: dict) -> list[str]:
    return sorted({
        str(info.get("hostapi", ""))
        for info in catalog.values()
        if info.get("hostapi")
    })


def _device_options_for_api(catalog: dict, capability: str, audio_api: str) -> Dict[int, str]:
    return {
        dev_id: label
        for dev_id, label in _device_options(catalog, capability).items()
        if not audio_api or catalog.get(dev_id, {}).get("hostapi") == audio_api
    }


def build_audio_setup_pane(config_file: str, show_live_capture=None):
    global _cal_meter_timer
    if _cal_meter_timer is not None:
        _cal_meter_timer.cancel()
        _cal_meter_timer = None

    parser = _read_config(config_file)
    catalog = get_devices_and_channels()
    inputs: Dict[tuple[str, str], Any] = {}
    fs_select = None
    auto_apply_task = None
    held_cal_level_dbfs = None

    with ui.column().classes("w-full h-full min-w-0 overflow-auto px-3 py-3 gap-4"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Audio Setup").classes("text-xl font-bold")

        device_selects = {}
        channel_selects = {}

        api_options = _audio_api_options(catalog)
        saved_in_api = _value(parser, "audio", "in_dev_hostapi", "")
        saved_out_api = _value(parser, "audio", "out_dev_hostapi", "")
        in_dev_id = _int_value(parser, "audio", "in_dev")
        out_dev_id = _int_value(parser, "audio", "out_dev")
        current_api = saved_in_api or saved_out_api
        if current_api not in api_options:
            current_api = catalog.get(in_dev_id, {}).get("hostapi") or catalog.get(out_dev_id, {}).get("hostapi") or ""
        if current_api not in api_options and api_options:
            current_api = api_options[0]

        api_select = ui.select(
            api_options,
            value=current_api,
            label="Audio API",
        ).classes("w-full").props("outlined dense")

        def selected_device_id(role: str):
            try:
                return int(device_selects[role].value)
            except (KeyError, TypeError, ValueError):
                return None

        def sync_device_metadata(role: str) -> None:
            try:
                dev_id = int(device_selects[role].value)
            except (KeyError, TypeError, ValueError):
                dev_id = None
            info = catalog.get(dev_id, {}) if dev_id is not None else {}
            inputs[("audio", f"{role}_dev_name")] = info.get("name", "")
            inputs[("audio", f"{role}_dev_hostapi")] = info.get("hostapi", "")

        def refresh_channels(role: str) -> None:
            capability = "input" if role == "in" else "output"
            opts = _channel_options(catalog, device_selects[role].value, capability)
            keys = ("in_ch_mic", "in_ch_loop") if role == "in" else ("out_ch_spkr", "out_ch_ref")
            for key in keys:
                select = channel_selects.get(key)
                if select is None:
                    continue
                current = select.value if select.value in opts else (opts[0] if opts else None)
                _set_select_options(select, opts, current)
            sync_device_metadata(role)

        def refresh_sample_rates() -> None:
            if fs_select is None:
                return
            rates = get_supported_sample_rates(selected_device_id("in"), selected_device_id("out"))
            current = fs_select.value
            if not rates and current:
                rates = [int(current)]
            elif rates and current not in rates:
                current = rates[0]
            _set_select_options(fs_select, _sample_rate_options(rates), current)

        def refresh_devices_for_api() -> None:
            selected_api = api_select.value or ""
            for role, capability in (("in", "input"), ("out", "output")):
                select = device_selects.get(role)
                if select is None:
                    continue
                opts = _device_options_for_api(catalog, capability, selected_api)
                current = select.value if select.value in opts else (next(iter(opts), None))
                _set_select_options(select, opts, current)
                refresh_channels(role)
            refresh_sample_rates()
            schedule_auto_apply()

        with ui.row().classes("w-full gap-3 items-start"):
            with ui.column().classes(
                "flex-1 gap-2 rounded border border-pink-200 bg-pink-50/60 p-2"
            ):
                ui.label("Input").classes("text-sm font-bold text-gray-600")
                in_options = _device_options_for_api(catalog, "input", current_api)
                in_value = in_dev_id
                if in_value not in in_options and in_options:
                    in_value = next(iter(in_options))
                in_select = ui.select(in_options, value=in_value, label="Input device").classes("w-full").props("outlined dense")
                device_selects["in"] = in_select
                inputs[("audio", "in_dev")] = in_select

                for key, label in (("in_ch_mic", "Mic input channel"), ("in_ch_loop", "Loopback input channel")):
                    opts = _channel_options(catalog, in_value, "input")
                    value = _int_value(parser, "audio", key)
                    if value not in opts and opts:
                        value = opts[0]
                    el = ui.select(opts, value=value, label=label).classes("w-full").props("outlined dense")
                    channel_selects[key] = el
                    inputs[("audio", key)] = el

            with ui.column().classes(
                "flex-1 gap-2 rounded border border-blue-200 bg-blue-50/60 p-2"
            ):
                ui.label("Output").classes("text-sm font-bold text-gray-600")
                out_options = _device_options_for_api(catalog, "output", current_api)
                out_value = out_dev_id
                if out_value not in out_options and out_options:
                    out_value = next(iter(out_options))
                out_select = ui.select(out_options, value=out_value, label="Output device").classes("w-full").props("outlined dense")
                device_selects["out"] = out_select
                inputs[("audio", "out_dev")] = out_select

                for key, label in (("out_ch_spkr", "Speaker output channel"), ("out_ch_ref", "Loopback output channel")):
                    opts = _channel_options(catalog, out_value, "output")
                    value = _int_value(parser, "audio", key)
                    if value not in opts and opts:
                        value = opts[0]
                    el = ui.select(opts, value=value, label=label).classes("w-full").props("outlined dense")
                    channel_selects[key] = el
                    inputs[("audio", key)] = el

        def on_device_change(role: str) -> None:
            refresh_channels(role)
            refresh_sample_rates()
            schedule_auto_apply()

        for role in ("in", "out"):
            device_selects[role].on("update:model-value", lambda _e, r=role: on_device_change(r))
            sync_device_metadata(role)

        api_select.on("update:model-value", lambda _e: refresh_devices_for_api())

        fs_value = _int_value(parser, "audio", "fs", 48000)
        fs_options = get_supported_sample_rates(selected_device_id("in"), selected_device_id("out")) or [fs_value]
        if fs_value not in fs_options:
            fs_value = fs_options[0]
        with ui.row().classes("w-full gap-3"):
            level_input = ui.number(
                "Output level (dBFS)",
                value=_float_value(parser, "sweep", "sweep_level_dbfs", -20.0),
                format="%.1f",
            ).classes("flex-1").props("outlined dense")
            fs_select = ui.select(_sample_rate_options(fs_options), value=fs_value, label="FS").classes("flex-1").props("outlined dense")
            inputs[("audio", "fs")] = fs_select

        with ui.expansion("Advanced audio settings", icon="tune").classes("w-full"):
            with ui.row().classes("w-full gap-3"):
                blocksize = ui.number("Blocksize", value=_int_value(parser, "audio", "blocksize", 2048), format="%d").classes("flex-1").props("outlined dense")
                wasapi = ui.switch("WASAPI exclusive", value=_parse_bool(_value(parser, "audio", "wasapi_exclusive", "False")))
                inputs[("audio", "blocksize")] = blocksize
                inputs[("audio", "wasapi_exclusive")] = wasapi

        ui.separator()
        ui.label("Sine Tone").classes("text-base font-bold")
        current_calibration = project.get_project_data().get("stage5_vars")
        current_scale = (
            current_calibration.get("frd_db_offset")
            if isinstance(current_calibration, dict)
            else None
        )
        current_spl = None
        with ui.row().classes("w-full items-center gap-2"):
            freq_input = ui.number("Frequency (Hz)", value=1000, format="%d").classes("w-36").props("outlined dense")
            dur_input = ui.number("Duration (s)", value=None, format="%.1f").classes("w-36").props('outlined dense placeholder="Optional"')
            with ui.button().props("round") as play_button:
                play_button_icon = ui.icon("play_arrow")
            control.register_sine_controls(level_input, freq_input, dur_input, play_button, play_button_icon)
            play_button.on("click", control.log_button_click("Play Sine", control.async_play_sine_task))
        ui.label("SPL Calibration").classes("text-base font-bold")
        with ui.row().classes("w-full items-center gap-2"):
            with ui.row().classes(
                "h-10 w-40 items-center justify-between rounded border border-pink-200 bg-pink-50/70 px-2 overflow-hidden"
            ):
                with ui.column().classes("gap-0 leading-tight shrink-0"):
                    ui.label("Mic Level").classes("text-[10px] font-semibold text-gray-500")
                    ui.label("dBFS(A)").classes("text-[10px] text-gray-500")
                cal_level_label = ui.label(_format_dbfs(held_cal_level_dbfs)).classes("font-mono text-sm whitespace-nowrap")
            spl_input = ui.number("Meter Reading (dB SPL)", value=current_spl, format="%.1f").classes("w-44").props("outlined dense")
            calc_offset_button = ui.button("Calibrate").props("dense")
            scale_input = ui.number("SPL dB Offset", value=current_scale, format="%.2f").classes("w-36").props("outlined dense")
            save_cal_button = ui.button("Save Cal", icon="save").props("dense")
            with ui.row().classes("items-center gap-1 text-blue-500 hover:text-blue-700 cursor-help transition-colors"):
                ui.icon("help_outline", size="18px")
                with ui.tooltip().props('content-class="bg-white text-gray-800 p-3 border border-gray-300 shadow-xl max-w-xs"'):
                    with ui.column().classes("gap-1 text-xs leading-snug"):
                        ui.label("1. Run the sine tone.")
                        ui.label("2. Wait for Mic Level dBFS(A) to settle; it holds when stopped.")
                        ui.label("3. Enter the physical SPL meter reading.")
                        ui.label("4. Press Calibrate to fill SPL dB Offset.")
                        ui.label("5. Press Save Cal, or enter a known offset manually and save.")

        cal_meter_peaks: list[float] = []

        def update_calibration_from_offset() -> bool:
            scale_value = _optional_float_value(scale_input.value)
            if scale_value is None:
                return False
            calibration = project.build_spl_calibration(None, None, scale_value)
            if calibration is None:
                return False
            project.update_spl_calibration(calibration)
            return True

        def calculate_spl_offset() -> None:
            if spl_input.value is None:
                ui.notify("Enter the SPL meter reading first", type="warning")
                return
            if held_cal_level_dbfs is None:
                ui.notify("Play the sine tone until the mic level readout appears", type="warning")
                return
            scale_input.value = float(spl_input.value) - held_cal_level_dbfs
            update_calibration_from_offset()

        def refresh_cal_meter() -> None:
            nonlocal held_cal_level_dbfs
            global _cal_meter_timer
            if getattr(cal_level_label, "is_deleted", False):
                if _cal_meter_timer is not None:
                    _cal_meter_timer.cancel()
                    _cal_meter_timer = None
                return
            state = get_audio_meter_state()
            if not state.get("active"):
                return
            value = _current_a_weighted_mic_peak_dbfs()
            if value is None:
                return
            cal_meter_peaks.append(value)
            if len(cal_meter_peaks) < 5:
                return
            held_cal_level_dbfs = max(cal_meter_peaks)
            cal_meter_peaks.clear()
            cal_level_label.set_text(_format_dbfs(held_cal_level_dbfs))

        _cal_meter_timer = BackgroundTimer(0.2, refresh_cal_meter)
        calc_offset_button.on(
            "click",
            control.log_button_click("Calibrate SPL dB Offset", calculate_spl_offset),
        )
        scale_input.on("update:model-value", lambda _e: update_calibration_from_offset())

        ui.separator()
        ui.label("Sweep Settings").classes("text-base font-bold")
        with ui.row().classes("w-full gap-3"):
            sweep_dur = ui.number("Sweep duration (s)", value=_float_value(parser, "sweep", "sweep_dur_s", 5.0), format="%.2f").classes("flex-1").props("outlined dense")
            num_sweeps = ui.number("No. sweeps", value=_int_value(parser, "sweep", "num_sweeps", 1), format="%d").classes("flex-1").props("outlined dense")
        hpf_raw = _value(parser, "sweep", "protect_hpf_hz", "None")
        hpf_enabled = hpf_raw.strip().lower() not in ("", "none", "0")
        with ui.row().classes("w-full gap-3 items-start"):
            with ui.column().classes("flex-1 gap-2"):
                hpf_enable = ui.switch("Protection HPF", value=hpf_enabled)
                hpf = ui.input(
                    "Protection HPF Hz",
                    value=_optional_float_text(parser, "sweep", "protect_hpf_hz"),
                ).classes("w-full").props('outlined dense placeholder="500Hz"')
                hpf_order = ui.number(
                    "HPF Order",
                    value=_int_value(parser, "sweep", "protect_hpf_order", 1),
                    format="%d",
                ).classes("w-full").props("outlined dense")
            with ui.column().classes("flex-1 gap-2 pt-10"):
                hpf_corr = ui.switch(
                    "HPF Inverse Correction",
                    value=_parse_bool(_value(parser, "sweep", "protect_hpf_correction", "False")),
                )
                hpf_cap = ui.number(
                    "HPF Correction Gain Cap",
                    value=_float_value(parser, "sweep", "protect_hpf_corr_db_cap", 12.0),
                    format="%.1f",
                ).classes("w-full").props("outlined dense")

        def update_hpf_field_state():
            hpf.set_enabled(bool(hpf_enable.value))
            hpf_order.set_enabled(bool(hpf_enable.value))
            hpf_corr.set_enabled(bool(hpf_enable.value))
            hpf_cap.set_enabled(bool(hpf_enable.value and hpf_corr.value))

        hpf_enable.on("update:model-value", lambda _e: update_hpf_field_state())
        hpf_corr.on("update:model-value", lambda _e: update_hpf_field_state())
        update_hpf_field_state()

        with ui.expansion("Advanced sweep settings", icon="tune").classes("w-full"):
            with ui.row().classes("w-full gap-3"):
                naming = ui.select(["tom", "dimitri"], value=_value(parser, "sweep", "naming_convention", "dimitri"), label="Naming").classes("flex-1").props("outlined dense")
                align = ui.switch("Align to first marker", value=_parse_bool(_value(parser, "sweep", "align_to_first_marker", "False")))
                debug = ui.switch("Debug saves", value=_parse_bool(_value(parser, "sweep", "debug_saves", "False")))
            with ui.row().classes("w-full gap-3"):
                pre_sil = ui.number("Pre silence (ms)", value=_float_value(parser, "sweep", "pre_sil_ms", 500.0), format="%.1f").classes("flex-1").props("outlined dense")
                post_sil = ui.number("Post silence (ms)", value=_float_value(parser, "sweep", "post_sil_ms", 500.0), format="%.1f").classes("flex-1").props("outlined dense")
                taper = ui.number("Mic tail taper (ms)", value=_float_value(parser, "sweep", "mic_tail_taper_ms", 20.0), format="%.1f").classes("flex-1").props("outlined dense")
            with ui.row().classes("w-full gap-3"):
                h2 = ui.input("H2 test dB", value=_value(parser, "sweep", "h2_test_db", "None")).classes("flex-1").props("outlined dense")
                h3 = ui.input("H3 test dB", value=_value(parser, "sweep", "h3_test_db", "None")).classes("flex-1").props("outlined dense")

        def save_audio_setup(notify: bool = False):
            values = {
                ("audio", "in_dev"): in_select.value,
                ("audio", "out_dev"): out_select.value,
                ("audio", "in_ch_mic"): channel_selects["in_ch_mic"].value,
                ("audio", "in_ch_loop"): channel_selects["in_ch_loop"].value,
                ("audio", "out_ch_spkr"): channel_selects["out_ch_spkr"].value,
                ("audio", "out_ch_ref"): channel_selects["out_ch_ref"].value,
                ("audio", "in_dev_name"): inputs[("audio", "in_dev_name")],
                ("audio", "in_dev_hostapi"): inputs[("audio", "in_dev_hostapi")],
                ("audio", "out_dev_name"): inputs[("audio", "out_dev_name")],
                ("audio", "out_dev_hostapi"): inputs[("audio", "out_dev_hostapi")],
                ("audio", "fs"): fs_select.value,
                ("audio", "blocksize"): blocksize.value,
                ("audio", "wasapi_exclusive"): wasapi.value,
                ("sweep", "naming_convention"): naming.value,
                ("sweep", "sweep_dur_s"): sweep_dur.value,
                ("sweep", "sweep_level_dbfs"): level_input.value,
                ("sweep", "num_sweeps"): num_sweeps.value,
                ("sweep", "protect_hpf_hz"): hpf.value if hpf_enable.value else "None",
                ("sweep", "protect_hpf_order"): hpf_order.value,
                ("sweep", "protect_hpf_correction"): hpf_corr.value,
                ("sweep", "protect_hpf_corr_db_cap"): hpf_cap.value,
                ("sweep", "align_to_first_marker"): align.value,
                ("sweep", "pre_sil_ms"): pre_sil.value,
                ("sweep", "post_sil_ms"): post_sil.value,
                ("sweep", "mic_tail_taper_ms"): taper.value,
                ("sweep", "debug_saves"): debug.value,
                ("sweep", "h2_test_db"): h2.value,
                ("sweep", "h3_test_db"): h3.value,
            }
            save_config_values(
                config_file,
                values,
                lambda: control.scanner_app.reload_config_ui(notify=False),
            )
            fresh = _read_config(config_file)
            project.update_audio_setup(
                _section_dict(fresh, "audio"),
                _section_dict(fresh, "sweep"),
            )
            update_calibration_from_offset()
            if notify:
                ui.notify("Audio setup saved", type="positive")
            if show_live_capture is not None:
                show_live_capture()

        async def save_spl_calibration() -> None:
            if not update_calibration_from_offset():
                ui.notify("Enter an SPL dB offset or calculate one first", type="warning")
                return
            if not await control.ensure_session_folder_selected():
                return
            project.save_project()
            ui.notify("SPL calibration saved", type="positive")

        save_cal_button.on(
            "click",
            control.log_button_click("Save SPL Calibration", save_spl_calibration),
        )

        def schedule_auto_apply() -> None:
            nonlocal auto_apply_task
            if auto_apply_task is not None:
                auto_apply_task.cancel()

            async def apply_later():
                try:
                    await asyncio.sleep(0.6)
                    save_audio_setup(notify=False)
                except asyncio.CancelledError:
                    pass

            auto_apply_task = asyncio.create_task(apply_later())

        auto_apply_controls = [
            *device_selects.values(),
            *channel_selects.values(),
            fs_select,
            blocksize,
            wasapi,
            level_input,
            sweep_dur,
            num_sweeps,
            hpf,
            hpf_enable,
            hpf_order,
            hpf_corr,
            hpf_cap,
            naming,
            align,
            debug,
            pre_sil,
            post_sil,
            taper,
            h2,
            h3,
        ]
        for element in auto_apply_controls:
            element.on("update:model-value", lambda _e: schedule_auto_apply())

        with ui.row().classes("w-full justify-start"):
            ui.button(
                "Test Sweep",
                icon="graphic_eq",
                on_click=control.log_button_click(
                    "Audio Setup Test Sweep",
                    control.async_test_sweep_task,
                ),
            ).props("color=primary")
