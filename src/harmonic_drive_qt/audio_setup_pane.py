"""Native Audio Setup pane."""

from __future__ import annotations

import configparser
import math
from pathlib import Path
from typing import Any

from . import project
from .config_support import (
    _channel_options,
    _device_options,
    _parse_bool,
    _sample_rate_options,
    _strip_inline_comment,
    save_config_values,
)
from nfs.audio import get_audio_meter_state, get_devices_and_channels, get_supported_sample_rates

from .backend import BackendManager, Worker
from .styles import primary_button, toggle_style
from .qt_compat import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QAbstractSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QThreadPool,
    QTimer,
    QVBoxLayout,
    QWidget,
    Qt,
    Signal,
)


PREFERRED_SAMPLE_RATE = 48000
DEFAULT_PROTECTION_HPF_HZ = "500.0"


def _normalize_decimal_text(value: str) -> str:
    return value.strip().replace(",", ".")


def _normalize_decimal_editor_text(widget: QLineEdit, text: str) -> None:
    if "," not in text:
        return
    cursor_pos = widget.cursorPosition()
    widget.setText(text.replace(",", "."))
    widget.setCursorPosition(cursor_pos)


def _read_config(config_file: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.optionxform = str  # type: ignore[assignment]
    parser.read(config_file)
    return parser


def _value(parser: configparser.ConfigParser, section: str, key: str, fallback: str = "") -> str:
    return _strip_inline_comment(parser.get(section, key, fallback=fallback))


def _int_value(parser, section: str, key: str, fallback: int = 0) -> int:
    try:
        return int(float(_normalize_decimal_text(_value(parser, section, key, str(fallback)))))
    except ValueError:
        return fallback


def _float_value(parser, section: str, key: str, fallback: float = 0.0) -> float:
    try:
        return float(_normalize_decimal_text(_value(parser, section, key, str(fallback))))
    except ValueError:
        return fallback


def _optional_float_text(parser, section: str, key: str, fallback: str = "") -> str:
    raw = _value(parser, section, key, fallback)
    if raw.strip().lower() in ("", "none"):
        return ""
    return _normalize_decimal_text(raw)


def _section_dict(parser: configparser.ConfigParser, section: str) -> dict[str, str]:
    if not parser.has_section(section):
        return {}
    return {key: _strip_inline_comment(value) for key, value in parser.items(section)}


def _audio_api_options(catalog: dict) -> list[str]:
    return sorted({
        str(info.get("hostapi", ""))
        for info in catalog.values()
        if info.get("hostapi")
    })


def _device_options_for_api(catalog: dict, capability: str, audio_api: str) -> dict[int, str]:
    return {
        dev_id: label
        for dev_id, label in _device_options(catalog, capability).items()
        if not audio_api or catalog.get(dev_id, {}).get("hostapi") == audio_api
    }


def _resolve_config_device_id(
    parser: configparser.ConfigParser,
    catalog: dict,
    role: str,
    capability: str,
) -> int:
    configured_id = _int_value(parser, "audio", f"{role}_dev")
    saved_name = _value(parser, "audio", f"{role}_dev_name", "")
    saved_api = _value(parser, "audio", f"{role}_dev_hostapi", "")
    if not saved_name:
        return configured_id

    target_name = saved_name.strip().casefold()
    target_api = saved_api.strip().casefold()
    channel_key = "input_channels" if capability == "input" else "output_channels"
    for dev_id, info in catalog.items():
        if not info.get(channel_key):
            continue
        if str(info.get("name", "")).strip().casefold() != target_name:
            continue
        if target_api and str(info.get("hostapi", "")).strip().casefold() != target_api:
            continue
        return int(dev_id)

    return configured_id


def _format_dbfs(value: float | None) -> str:
    if value is None or value <= -119.0:
        return "-inf dBFS"
    return f"{value:.1f} dBFS"


def _format_vrms(value: float | None) -> str:
    if value is None:
        return ""
    if value < 1.0:
        return f"{value * 1000.0:.0f} mVrms"
    return f"{value:.2f} Vrms"


def _optional_stage5_float(stage5_vars: dict[str, Any], key: str) -> float | None:
    try:
        value = stage5_vars.get(key)
        return None if value is None else float(_normalize_decimal_text(str(value)))
    except (TypeError, ValueError):
        return None


SPL_WEIGHTING_OPTIONS = {
    "a_weighted_inputs": "A",
    "c_weighted_inputs": "C",
    "inputs": "Unweighted",
}
DEFAULT_SPL_WEIGHTING = "c_weighted_inputs"


def _spl_weighting_key(value: Any, fallback: str = DEFAULT_SPL_WEIGHTING) -> str:
    text = str(value or "").strip()
    if text in SPL_WEIGHTING_OPTIONS:
        return text
    target = text.casefold()
    for key, label in SPL_WEIGHTING_OPTIONS.items():
        if label.casefold() == target:
            return key
    return fallback


def _spl_weighting_label(value: Any) -> str:
    return SPL_WEIGHTING_OPTIONS.get(_spl_weighting_key(value), SPL_WEIGHTING_OPTIONS[DEFAULT_SPL_WEIGHTING])


def _weighting_curve_image_path() -> str:
    return str(Path(__file__).resolve().parents[2] / "images" / "frequency_weighting.png")


def _qt_icon_path(name: str) -> str:
    return (Path(__file__).resolve().parent / "icons" / name).as_posix()


def _output_level_spin_style() -> str:
    up_icon = _qt_icon_path("spin-chevron-up.svg")
    down_icon = _qt_icon_path("spin-chevron-down.svg")
    return (
        "QDoubleSpinBox#OutputLevelSpin {"
        "background: #ffffff;"
        "border: 1px solid #bfc8d4;"
        "border-radius: 4px;"
        "color: #111827;"
        "font-size: 10pt;"
        "min-height: 22px;"
        "padding: 2px 54px 2px 8px;"
        "}"
        "QDoubleSpinBox#OutputLevelSpin::up-button {"
        "subcontrol-origin: border;"
        "subcontrol-position: center right;"
        "width: 22px;"
        "height: 20px;"
        "right: 28px;"
        "border: none;"
        "background: transparent;"
        "}"
        "QDoubleSpinBox#OutputLevelSpin::down-button {"
        "subcontrol-origin: border;"
        "subcontrol-position: center right;"
        "width: 22px;"
        "height: 20px;"
        "right: 5px;"
        "border: none;"
        "background: transparent;"
        "}"
        "QDoubleSpinBox#OutputLevelSpin::up-button:hover,"
        "QDoubleSpinBox#OutputLevelSpin::down-button:hover {"
        "background: #f3f4f6;"
        "border-radius: 3px;"
        "}"
        f"QDoubleSpinBox#OutputLevelSpin::up-arrow {{ image: url({up_icon}); width: 13px; height: 13px; }}"
        f"QDoubleSpinBox#OutputLevelSpin::down-arrow {{ image: url({down_icon}); width: 13px; height: 13px; }}"
    )


class AudioSetupPane(QWidget):
    saved = Signal()

    def __init__(self, backend: BackendManager, config_file: str, show_live_capture=None, parent=None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.config_file = config_file
        self.show_live_capture = show_live_capture
        self.pool = QThreadPool.globalInstance()
        self.catalog = get_devices_and_channels()
        self.auto_apply_enabled = False
        self.sine_running = False
        self.held_cal_level_dbfs: float | None = None
        self.held_voltage_input_dbfs: float | None = None
        self.cal_meter_readings: list[float] = []
        self.voltage_meter_readings: list[float] = []
        self._build_ui()
        self.auto_apply_enabled = True

        self.auto_apply_timer = QTimer(self)
        self.auto_apply_timer.setSingleShot(True)
        self.auto_apply_timer.setInterval(600)
        self.auto_apply_timer.timeout.connect(lambda: self.save_audio_setup(notify=False))

        self.cal_timer = QTimer(self)
        self.cal_timer.setInterval(200)
        self.cal_timer.timeout.connect(self.refresh_cal_meter)
        self.cal_timer.start()

    def _build_ui(self) -> None:
        parser = _read_config(self.config_file)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet("background-color: #ffffff;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 0, 12, 18)
        layout.setSpacing(12)
        scroll.setWidget(content)
        root.addWidget(scroll)

        title = QLabel("Audio Setup")
        title.setStyleSheet("font-size: 20px; font-weight: 800; color: #000000;")
        layout.addWidget(title)

        self.api_select = QComboBox()
        api_options = _audio_api_options(self.catalog)
        in_dev_id = _resolve_config_device_id(parser, self.catalog, "in", "input")
        out_dev_id = _resolve_config_device_id(parser, self.catalog, "out", "output")
        current_api = (
            _value(parser, "audio", "in_dev_hostapi", "")
            or _value(parser, "audio", "out_dev_hostapi", "")
            or self.catalog.get(in_dev_id, {}).get("hostapi")
            or self.catalog.get(out_dev_id, {}).get("hostapi")
            or (api_options[0] if api_options else "")
        )
        self._set_combo_items(
            self.api_select,
            api_options,
            current_api,
            styled_field=True,
            fit_to_contents=True,
        )
        api_field = self._labeled("Audio API", self.api_select)
        api_field.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        top_settings_row = QHBoxLayout()
        top_settings_row.setSpacing(8)
        top_settings_row.addWidget(api_field, 0, Qt.AlignmentFlag.AlignLeft)
        top_settings_row.addSpacing(8)
        layout.addLayout(top_settings_row)

        device_row = QHBoxLayout()
        self.in_device = QComboBox()
        self.out_device = QComboBox()
        self.in_mic_channel = QComboBox()
        self.in_loop_channel = QComboBox()
        self.out_speaker_channel = QComboBox()
        self.out_ref_channel = QComboBox()
        device_row.addWidget(self._device_group("Input", self.in_device, self.in_mic_channel, self.in_loop_channel))
        device_row.addWidget(self._device_group("Output", self.out_device, self.out_speaker_channel, self.out_ref_channel, output=True))

        self._populate_devices("in", in_dev_id)
        self._populate_devices("out", out_dev_id)
        self._populate_channels("in")
        self._populate_channels("out")
        self._set_combo_data(self.in_mic_channel, _int_value(parser, "audio", "in_ch_mic", 0))
        self._set_combo_data(self.in_loop_channel, _int_value(parser, "audio", "in_ch_loop", 1))
        self._set_combo_data(self.out_speaker_channel, _int_value(parser, "audio", "out_ch_spkr", 0))
        self._set_combo_data(self.out_ref_channel, _int_value(parser, "audio", "out_ch_ref", 1))

        self.level = self._spin(_float_value(parser, "sweep", "sweep_level_dbfs", -20.0), -120, 0, "", 1)
        self.level.setObjectName("OutputLevelSpin")
        self.level.setProperty("skipSelectAllOnFocus", True)
        self.level.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self.level.setSingleStep(1.0)
        self.level.setStyleSheet(_output_level_spin_style())
        self.level.setFixedHeight(32)
        self.level.setFixedWidth(136)
        self.sweep_level_voltage = QLabel("")
        self.sweep_level_voltage.setStyleSheet("color: #475569; font-weight: 700;")
        self.sweep_level_voltage.setVisible(False)
        self.fs = QComboBox()
        self.fs.setProperty("fitToContents", True)
        self._populate_sample_rates(_int_value(parser, "audio", "fs", 48000))
        top_settings_row.addWidget(self._output_level_field())
        top_settings_row.addWidget(self._labeled_with_unit("FS", self.fs, "Hz"))
        top_settings_row.addStretch(1)
        layout.addWidget(self.sweep_level_voltage)
        layout.addLayout(device_row)

        advanced_audio = QGroupBox("Advanced audio settings")
        advanced_audio.setCheckable(True)
        advanced_audio.setChecked(False)
        adv_grid = QGridLayout(advanced_audio)
        self.blocksize = self._spin(_int_value(parser, "audio", "blocksize", 2048), 1, 65536, "", 0)
        self.wasapi = QCheckBox("WASAPI exclusive")
        self.wasapi.setChecked(_parse_bool(_value(parser, "audio", "wasapi_exclusive", "False")))
        self.wasapi.setStyleSheet(toggle_style())
        blocksize_label = QLabel("Blocksize")
        adv_grid.addWidget(blocksize_label, 0, 0)
        adv_grid.addWidget(self.blocksize, 0, 1)
        adv_grid.addWidget(self.wasapi, 0, 2)
        self._collapse_group_children(
            advanced_audio,
            [blocksize_label, self.blocksize, self.wasapi],
        )
        layout.addWidget(advanced_audio)

        layout.addWidget(self._build_sine_group(parser))
        layout.addWidget(self._build_spl_group())
        self.update_sweep_voltage_label()
        layout.addWidget(self._build_sweep_group(parser))
        layout.addStretch(1)

        self._connect_auto_apply_controls()
        self.api_select.currentIndexChanged.connect(self.refresh_devices_for_api)
        self.in_device.currentIndexChanged.connect(lambda: self.on_device_change("in"))
        self.out_device.currentIndexChanged.connect(lambda: self.on_device_change("out"))
        self.level.valueChanged.connect(self.update_sweep_voltage_label)

    def _device_group(self, title: str, device: QComboBox, first: QComboBox, second: QComboBox, output: bool = False) -> QFrame:
        group = QFrame()
        color = "#bfdbfe" if output else "#fbcfe8"
        fill = "#f1f7ff" if output else "#fff6fb"
        text_color = "#1e3a8a" if output else "#831843"
        group.setStyleSheet(f"QFrame {{ background: {fill}; border: 1px solid {color}; border-radius: 4px; }}")
        
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        
        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {text_color}; font-weight: bold; font-size: 14px; border: none;")
        layout.addWidget(lbl)
        
        def add_field(label_text, widget):
            w = QWidget()
            w.setStyleSheet(f"background: {fill}; border: 1px solid {color}; border-radius: 4px;")
            l = QHBoxLayout(w)
            l.setContentsMargins(8, 3, 8, 3)
            l.setSpacing(8)
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #64748b; font-size: 10px; border: none;")
            lbl.setMinimumWidth(126)
            lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            widget.setProperty("toneFill", fill)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._apply_tinted_combo_style(widget)
            l.addWidget(lbl)
            l.addWidget(widget, 1)
            layout.addWidget(w)
            
        add_field(f"{title} device", device)
        add_field("Speaker output channel" if output else "Mic input channel", first)
        add_field("Loopback output channel" if output else "Loopback input channel", second)
        
        return group

    def _build_sine_group(self, parser) -> QGroupBox:
        group = QGroupBox("Sine Tone")
        row = QHBoxLayout(group)
        row.setSpacing(8)
        self.sine_freq = self._spin(1000, 10, 40000, "", 0)
        self.sine_freq.setFixedWidth(90)
        self.sine_duration_text = QLineEdit()
        self.sine_duration_text.setPlaceholderText("Optional")
        self.sine_duration_text.setFixedWidth(120)
        self._normalize_decimal_editor(self.sine_duration_text)
        self.sine_button = QPushButton("Play Sine")
        primary_button(self.sine_button)
        self.sine_button.setFixedSize(88, 36)
        self.sine_button.clicked.connect(self.toggle_sine)
        row.addWidget(self._labeled_with_unit("Frequency", self.sine_freq, "Hz"))
        row.addWidget(self._labeled_with_unit("Duration", self.sine_duration_text, "s"))
        row.addWidget(self.sine_button)
        row.setAlignment(self.sine_button, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        return group

    def _help_badge(self, tooltip_text: str) -> QLabel:
        badge = QLabel("?")
        badge.setFixedSize(18, 18)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setToolTip(tooltip_text)
        badge.setStyleSheet(
            "background: #eef6ff; border: 1px solid #7db2e8; border-radius: 9px; "
            "color: #3978bd; font-size: 12px; font-weight: 900;"
        )
        return badge

    def _method_header(self, title: str, tooltip_text: str) -> QWidget:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(title)
        label.setStyleSheet("font-weight: 800; color: #0f172a;")
        layout.addWidget(label)
        layout.addWidget(self._help_badge(tooltip_text))
        layout.addStretch(1)
        return wrapper

    def _build_spl_group(self) -> QGroupBox:
        curve_path = _weighting_curve_image_path()
        spl_tooltip_text = (
            "<div style='color: #111827; font-weight: 400; font-size: 12px;'>"
            f"<img src='{curve_path}'><br>"
            "<div style='white-space: pre;'>"
            "Method Two - SPL Meter Calibration Mode<br><br>"
            "This method calibrates the input/SPL offset only.<br><br>"
            "1. Choose the weighting that matches your physical SPL meter.<br>"
            "2. Play the 1 kHz sine from the speaker with the mic connected.<br>"
            "3. Put the SPL meter at the same distance as the mic, then enter its reading in Meter Reading.<br>"
            "4. Click Calibrate to calculate the difference between the mic input RMS level and the meter reading.<br>"
            "   This becomes the calibration dB offset."
            "</div>"
            "</div>"
        )
        group = QGroupBox("Calibration")
        group.setCheckable(True)
        group.setChecked(False)
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(8)
        current_calibration = project.get_system_calibration(self.config_file)
        current_scale = current_spl = current_weighting = None
        if isinstance(current_calibration, dict):
            current_scale = current_calibration.get("frd_db_offset")
            current_spl = current_calibration.get("spl_db")
            current_weighting = current_calibration.get("spl_meter_weighting")

        method_one = self._build_voltage_calibration_group(current_calibration if isinstance(current_calibration, dict) else {})

        method_two = QGroupBox("")
        method_two_layout = QVBoxLayout(method_two)
        method_two_layout.setSpacing(8)
        method_two_layout.addWidget(self._method_header("Method Two - SPL Meter Calibration Mode", spl_tooltip_text))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.held_cal_level_dbfs = None
        self.cal_level = QLabel(_format_dbfs(None))
        self.cal_level.setStyleSheet("font-family: Consolas; font-weight: 700; font-size: 15px;")
        self.cal_weighting = QComboBox()
        self._set_combo_items(
            self.cal_weighting,
            {key: label for key, label in SPL_WEIGHTING_OPTIONS.items()},
            _spl_weighting_key(current_weighting),
            styled_field=True,
            fit_to_contents=True,
        )
        self.cal_weighting.setMinimumContentsLength(len("Unweighted"))
        self.cal_weighting.setFixedWidth(self.cal_weighting.fontMetrics().horizontalAdvance("Unweighted") + 30)
        self.cal_weighting.currentIndexChanged.connect(self.on_cal_weighting_changed)
        try:
            spl_value = 0.0 if current_spl is None else float(_normalize_decimal_text(str(current_spl)))
        except (TypeError, ValueError):
            spl_value = 0.0
        self.spl_reading = self._spin(spl_value, 0, 200, "", 1)
        self.spl_reading.setSpecialValueText("")
        self.spl_reading.setFixedWidth(self.spl_reading.fontMetrics().horizontalAdvance("100.2") + 34)
        self.spl_offset = self._spin(float(_normalize_decimal_text(str(current_scale or 0.0))), -200, 200, "", 2)
        self.spl_offset.setFixedWidth(self.spl_offset.fontMetrics().horizontalAdvance("100.2") + 34)
        calc = QPushButton("Calibrate")
        primary_button(calc)
        calc.setFixedSize(100, 36)
        calc.clicked.connect(self.calculate_spl_offset)
        save = QPushButton("Save Input Cal")
        primary_button(save)
        save.setFixedSize(112, 36)
        save.clicked.connect(self.save_spl_calibration)
        cal_box = QWidget()
        cal_box.setStyleSheet("border: 1px solid #fbcfe8; border-radius: 4px; background: #fff6fb;")
        cal_layout = QHBoxLayout(cal_box)
        cal_layout.setContentsMargins(8, 2, 8, 2)
        self.cal_label = QLabel()
        self.cal_label.setStyleSheet("color: #831843; font-size: 10px; font-weight: 700;")
        self.update_cal_level_label()
        cal_layout.addWidget(self.cal_label)
        cal_layout.addWidget(self.cal_level)
        row.addWidget(cal_box)
        row.addWidget(self._labeled("Weighting", self.cal_weighting))
        row.addWidget(self._labeled_with_unit("Meter Reading", self.spl_reading, "dB SPL"))
        row.addWidget(calc)
        row.setAlignment(calc, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self._labeled_with_unit("SPL Offset", self.spl_offset, "dB"))
        row.addWidget(save)
        row.setAlignment(save, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        method_two_layout.addLayout(row)
        group_layout.addWidget(method_one)
        group_layout.addWidget(method_two)
        self._collapse_group_children(group, [method_one, method_two])
        return group

    def _build_voltage_calibration_group(self, current_calibration: dict[str, Any]) -> QGroupBox:
        group = QGroupBox("")
        tooltip_text = (
            "<div style='color: #111827; font-weight: 400; font-size: 12px;'>"
            "<div style='white-space: pre;'>"
            "Method One - Voltage Calibration Mode<br><br>"
            "Output voltage calibration:<br>"
            "1. Play sine from the selected output.<br>"
            "2. Measure Vrms with a trueRMS meter and enter it under Measured output.<br><br>"
            "Input voltage calibration:<br>"
            "3. Loop the signal into the selected mic input.<br>"
            "4. Enter mic sensitivity in mV/Pa.<br>"
            "5. Save input voltage calibration.<br><br>"
            "The calibration is derived from the measured sine output voltage, the input level in dBFS when driven by that signal, and the mic sensitivity in mV/Pa (mV at 94 dB SPL).<br><br>"
            "Calibration is only valid while the interface gain/settings remain unchanged."
            "</div>"
            "</div>"
        )
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        output_cal = current_calibration.get("output_voltage_calibration")
        if not isinstance(output_cal, dict):
            output_cal = {}
        voltage_cal = current_calibration.get("voltage_calibration")
        if not isinstance(voltage_cal, dict):
            voltage_cal = {}

        layout.addWidget(self._method_header("Method One - Voltage Calibration Mode", tooltip_text))
        output_title = QLabel("1) Output Voltage Calibration")
        output_title.setStyleSheet("font-weight: 800; color: #0f172a;")
        output_row = QHBoxLayout()
        self.voltage_output_vrms = self._spin(_optional_stage5_float(output_cal, "output_vrms") or 0.0, 0.0, 1000.0, "", 4)
        self.voltage_output_vrms.setFixedWidth(110)
        amp_gain = _optional_stage5_float(output_cal, "amplifier_gain_db")
        self.voltage_amp_gain = self._spin(0.0 if amp_gain is None else amp_gain, -120.0, 120.0, "", 1)
        self.voltage_amp_gain.setFixedWidth(90)
        self.voltage_amp_gain.setSpecialValueText("")
        save_output = QPushButton("Save Output Cal")
        primary_button(save_output)
        save_output.setFixedSize(128, 36)
        save_output.clicked.connect(self.save_output_voltage_calibration)
        output_vrms_field = self._labeled_with_unit("Measured output", self.voltage_output_vrms, "Vrms")
        amp_gain_field = self._labeled_with_unit("Amplifier gain", self.voltage_amp_gain, "dB")
        output_row.addWidget(output_vrms_field)
        output_row.addWidget(amp_gain_field)
        output_row.addWidget(save_output)
        output_row.setAlignment(save_output, Qt.AlignmentFlag.AlignBottom)
        output_row.addStretch(1)

        input_title = QLabel("2) Input Voltage Calibration")
        input_title.setStyleSheet("font-weight: 800; color: #0f172a;")
        input_row = QHBoxLayout()
        saved_voltage_input = _optional_stage5_float(voltage_cal, "reference_input_rms_dbfs")
        self.held_voltage_input_dbfs = saved_voltage_input
        self.voltage_input_level = QLabel(_format_dbfs(saved_voltage_input))
        self.voltage_input_level.setStyleSheet("font-family: Consolas; font-weight: 700; font-size: 15px;")
        voltage_level_box = QWidget()
        voltage_level_box.setStyleSheet("border: 1px solid #fbcfe8; border-radius: 4px; background: #fff6fb;")
        voltage_level_layout = QHBoxLayout(voltage_level_box)
        voltage_level_layout.setContentsMargins(8, 2, 8, 2)
        self.voltage_input_label = QLabel("Input RMS\ndBFS")
        self.voltage_input_label.setStyleSheet("color: #831843; font-size: 10px; font-weight: 700;")
        voltage_level_layout.addWidget(self.voltage_input_label)
        voltage_level_layout.addWidget(self.voltage_input_level)
        sensitivity = _optional_stage5_float(voltage_cal, "microphone_sensitivity_mv_pa")
        self.mic_sensitivity = self._spin(0.0 if sensitivity is None else sensitivity, 0.0, 100000.0, "", 3)
        self.mic_sensitivity.setFixedWidth(112)
        calibrate_input = QPushButton("Save Input Cal")
        primary_button(calibrate_input)
        calibrate_input.setFixedSize(112, 36)
        calibrate_input.clicked.connect(self.save_voltage_spl_calibration)
        input_row.addWidget(voltage_level_box)
        mic_sensitivity_field = self._labeled_with_unit("Mic sensitivity", self.mic_sensitivity, "mV/Pa")
        input_row.addWidget(mic_sensitivity_field)
        input_row.addWidget(calibrate_input)
        input_row.setAlignment(calibrate_input, Qt.AlignmentFlag.AlignBottom)
        input_row.addStretch(1)

        layout.addWidget(output_title)
        layout.addLayout(output_row)
        layout.addWidget(input_title)
        layout.addLayout(input_row)
        self.voltage_output_vrms.valueChanged.connect(self.update_sweep_voltage_label)
        self.voltage_amp_gain.valueChanged.connect(self.update_sweep_voltage_label)
        return group

    def _build_sweep_group(self, parser) -> QGroupBox:
        group = QGroupBox("Sweep Settings")
        layout = QVBoxLayout(group)
        grid = QGridLayout()
        self.sweep_dur = self._spin(_float_value(parser, "sweep", "sweep_dur_s", 5.0), 0.01, 3600, "", 2)
        self.num_sweeps = self._spin(_int_value(parser, "sweep", "num_sweeps", 1), 1, 100, "", 0)
        hpf_raw = _value(parser, "sweep", "protect_hpf_hz", "None")
        self.hpf_enable = QCheckBox("Protection HPF")
        self.hpf_enable.setChecked(hpf_raw.strip().lower() not in ("", "none", "0"))
        self.hpf_enable.setStyleSheet(toggle_style())
        self.hpf = QLineEdit(_optional_float_text(parser, "sweep", "protect_hpf_hz") or DEFAULT_PROTECTION_HPF_HZ)
        self.hpf.setFixedWidth(120)
        self._normalize_decimal_editor(self.hpf)
        self.hpf_order = self._spin(_int_value(parser, "sweep", "protect_hpf_order", 1), 1, 8, "", 0)
        self.hpf_order.setFixedWidth(86)
        self.hpf_corr = QCheckBox("HPF Inverse Correction")
        self.hpf_corr.setChecked(_parse_bool(_value(parser, "sweep", "protect_hpf_correction", "False")))
        self.hpf_corr.setStyleSheet(toggle_style())
        self.hpf_cap = self._spin(_float_value(parser, "sweep", "protect_hpf_corr_db_cap", 12.0), 0, 80, "", 1)
        self.hpf_cap.setFixedWidth(86)
        self.sweep_dur.setFixedWidth(120)
        self.num_sweeps.setFixedWidth(86)
        grid.addWidget(QLabel("Sweep duration"), 0, 0)
        grid.addWidget(self._labeled_with_unit("", self.sweep_dur, "s"), 0, 1)
        grid.addWidget(QLabel("No. sweeps"), 0, 2)
        grid.addWidget(self.num_sweeps, 0, 3)
        self.hpf_frequency_field = self._labeled_with_unit("", self.hpf, "Hz")
        self.hpf_order_label = QLabel("HPF Order")
        self.hpf_cap_label = QLabel("Correction Gain Cap")
        grid.addWidget(self.hpf_enable, 1, 0)
        grid.addWidget(self.hpf_frequency_field, 1, 1)
        grid.addWidget(self.hpf_order_label, 1, 2)
        grid.addWidget(self.hpf_order, 1, 3)
        grid.addWidget(self.hpf_corr, 2, 0)
        grid.addWidget(self.hpf_cap_label, 2, 2)
        self.hpf_cap_field = self._labeled_with_unit("", self.hpf_cap, "dB")
        grid.addWidget(self.hpf_cap_field, 2, 3)
        layout.addLayout(grid)

        advanced = QGroupBox("Advanced sweep settings")
        advanced.setCheckable(True)
        advanced.setChecked(False)
        adv = QGridLayout(advanced)
        self.naming = QComboBox()
        self.naming.addItems(["tom", "dimitri"])
        self.naming.setCurrentText(_value(parser, "sweep", "naming_convention", "dimitri"))
        self.align = QCheckBox("Align to first marker")
        self.align.setChecked(_parse_bool(_value(parser, "sweep", "align_to_first_marker", "False")))
        self.align.setStyleSheet(toggle_style())
        self.debug = QCheckBox("Debug saves")
        self.debug.setChecked(_parse_bool(_value(parser, "sweep", "debug_saves", "False")))
        self.debug.setStyleSheet(toggle_style())
        self.pre_sil = self._spin(_float_value(parser, "sweep", "pre_sil_ms", 500.0), 0, 60000, " ms", 1)
        self.post_sil = self._spin(_float_value(parser, "sweep", "post_sil_ms", 500.0), 0, 60000, " ms", 1)
        self.taper = self._spin(_float_value(parser, "sweep", "mic_tail_taper_ms", 20.0), 0, 60000, " ms", 1)
        self.h2 = QLineEdit(_normalize_decimal_text(_value(parser, "sweep", "h2_test_db", "None")))
        self.h3 = QLineEdit(_normalize_decimal_text(_value(parser, "sweep", "h3_test_db", "None")))
        self._normalize_decimal_editor(self.h2)
        self._normalize_decimal_editor(self.h3)
        fields = [
            ("Naming", self.naming), ("", self.align), ("", self.debug),
            ("Pre silence", self.pre_sil), ("Post silence", self.post_sil), ("Mic tail taper", self.taper),
            ("H2 test dB", self.h2), ("H3 test dB", self.h3),
        ]
        advanced_children = []
        for index, (label, widget) in enumerate(fields):
            row = index // 3
            col = (index % 3) * 2
            if label:
                label_widget = QLabel(label)
                adv.addWidget(label_widget, row, col)
                adv.addWidget(widget, row, col + 1)
                advanced_children.extend([label_widget, widget])
            else:
                adv.addWidget(widget, row, col, 1, 2)
                advanced_children.append(widget)
        layout.addWidget(advanced)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        test = QPushButton("   TEST SWEEP")
        test.setFixedSize(160, 40)
        primary_button(test)
        test.clicked.connect(self.test_sweep)
        action_row.addWidget(test)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        self._collapse_group_children(advanced, advanced_children)
        self.hpf_enable.stateChanged.connect(self.update_hpf_field_state)
        self.hpf_corr.stateChanged.connect(self.update_hpf_field_state)
        self.update_hpf_field_state()
        return group

    def _collapse_group_children(self, group: QGroupBox, children: list[QWidget]) -> None:
        def apply_collapsed(checked: bool) -> None:
            for child in children:
                child.setVisible(checked)

        group.toggled.connect(apply_collapsed)
        apply_collapsed(group.isChecked())

    def _spin(self, value: float, minimum: float, maximum: float, suffix: str, decimals: int) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setSuffix(suffix)
        spin.setKeyboardTracking(False)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        original_focus = spin.focusInEvent

        def focus_in(event, spin=spin, original_focus=original_focus) -> None:
            original_focus(event)
            if not spin.property("skipSelectAllOnFocus"):
                QTimer.singleShot(0, spin.selectAll)

        spin.focusInEvent = focus_in  # type: ignore[method-assign]
        return spin

    def _normalize_decimal_editor(self, widget: QLineEdit) -> None:
        widget.textEdited.connect(lambda text, widget=widget: _normalize_decimal_editor_text(widget, text))

    def _labeled(self, label: str, widget: QWidget) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setFixedHeight(14)
        layout.addWidget(lbl)
        layout.addWidget(widget)
        return wrapper

    def _labeled_with_unit(self, label: str, widget: QWidget, unit: str) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        if label:
            lbl = QLabel(label)
            lbl.setFixedHeight(14)
            layout.addWidget(lbl)
            setattr(widget, "_field_label", lbl)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        unit_label = QLabel(unit)
        unit_label.setStyleSheet("color: #64748b; font-weight: 700; border: none; padding-bottom: 2px;")
        setattr(widget, "_unit_label", unit_label)
        row.addWidget(widget)
        row.addWidget(unit_label)
        row.setAlignment(unit_label, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(row)
        return wrapper

    def _output_level_field(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel("Output level")
        label.setFixedHeight(14)
        layout.addWidget(label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        unit_label = QLabel("dBFS")
        unit_label.setStyleSheet("color: #64748b; font-weight: 700; border: none; padding-bottom: 2px;")
        setattr(self.level, "_field_label", label)
        setattr(self.level, "_unit_label", unit_label)

        row.addWidget(self.level)
        row.addWidget(unit_label)
        row.setAlignment(unit_label, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(row)
        return wrapper

    def _apply_tinted_combo_style(self, widget: QWidget) -> None:
        fill = widget.property("toneFill")
        if not fill:
            widget.setStyleSheet(
                "QComboBox {"
                "background: #ffffff;"
                "border: 1px solid #bfc8d4;"
                "border-radius: 4px;"
                "color: #111827;"
                "min-height: 24px;"
                "padding: 4px 8px;"
                "}"
                "QComboBox:disabled {"
                "background: #f1f5f9;"
                "border-color: #d8dee8;"
                "color: #94a3b8;"
                "}"
                "QComboBox::drop-down { border: 0; width: 0px; }"
                "QComboBox::down-arrow { image: none; width: 0px; height: 0px; }"
            )
            return
        widget.setStyleSheet(
            f"QComboBox {{ border: none; background: {fill}; font-size: 10pt; color: #0f172a; padding: 1px 18px 1px 0; }}"
            "QComboBox::drop-down { border: none; width: 16px; }"
        )

    def _connect_auto_apply_controls(self) -> None:
        immediate_controls = [
            self.in_mic_channel, self.in_loop_channel, self.out_speaker_channel, self.out_ref_channel,
            self.fs, self.wasapi, self.hpf_enable, self.hpf_corr, self.naming,
            self.align, self.debug,
        ]
        commit_controls = [
            self.blocksize, self.sweep_dur, self.num_sweeps,
            self.hpf_order, self.hpf_cap, self.pre_sil, self.post_sil, self.taper,
        ]
        for control in immediate_controls:
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self.apply_audio_setup_now)
            elif isinstance(control, QCheckBox):
                control.stateChanged.connect(self.apply_audio_setup_now)
        for control in commit_controls:
            control.editingFinished.connect(self.apply_audio_setup_now)
        for control in (self.hpf, self.h2, self.h3):
            control.editingFinished.connect(self.apply_audio_setup_now)
        self.level.valueChanged.connect(self.apply_output_level_change)

    def _combo_data(self, combo: QComboBox) -> Any:
        return combo.currentData()

    def _set_combo_items(
        self,
        combo: QComboBox,
        options: dict | list,
        current=None,
        styled_field: bool = True,
        fit_to_contents: bool | None = None,
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        if isinstance(options, dict):
            for key, label in options.items():
                combo.addItem(str(label), key)
        else:
            for item in options:
                combo.addItem(str(item), item)
        self._set_combo_data(combo, current)
        combo.blockSignals(False)
        if styled_field:
            self._apply_tinted_combo_style(combo)
        should_fit = combo.property("fitToContents") if fit_to_contents is None else fit_to_contents
        if should_fit:
            combo.setProperty("fitToContents", True)
            self._fit_combo_to_contents(combo, options)

    def _set_combo_data(self, combo: QComboBox, value) -> None:
        if value is None:
            return
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def _fit_combo_to_contents(self, combo: QComboBox, options: list | dict) -> None:
        if isinstance(options, dict):
            labels = [str(label) for label in options.values()]
        else:
            labels = [str(item) for item in options]
        longest = max(labels, key=len, default="")
        combo.setMinimumContentsLength(max(12, len(longest)))
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        text_width = combo.fontMetrics().horizontalAdvance(longest)
        combo.setFixedWidth(max(combo.sizeHint().width(), text_width + 24))

    def selected_device_id(self, role: str) -> int | None:
        value = self._combo_data(self.in_device if role == "in" else self.out_device)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _populate_devices(self, role: str, current=None) -> None:
        selected_api = self.api_select.currentText()
        combo = self.in_device if role == "in" else self.out_device
        capability = "input" if role == "in" else "output"
        opts = _device_options_for_api(self.catalog, capability, selected_api)
        if current not in opts and opts:
            current = next(iter(opts))
        self._set_combo_items(combo, opts, current)

    def _populate_channels(self, role: str, reset: bool = False) -> None:
        capability = "input" if role == "in" else "output"
        dev_id = self.selected_device_id(role)
        opts = _channel_options(self.catalog, dev_id, capability)
        targets = (
            (self.in_mic_channel, self.in_loop_channel)
            if role == "in"
            else (self.out_speaker_channel, self.out_ref_channel)
        )
        for index, combo in enumerate(targets):
            current = opts[min(index, len(opts) - 1)] if reset and opts else combo.currentData()
            if current not in opts and opts:
                current = opts[min(index, len(opts) - 1)]
            self._set_combo_items(combo, opts, current)

    def _populate_sample_rates(self, current=None, prefer_default: bool = False) -> None:
        rates = get_supported_sample_rates(self.selected_device_id("in"), self.selected_device_id("out"))
        if not rates and current:
            rates = [int(current)]
        elif rates:
            if prefer_default and PREFERRED_SAMPLE_RATE in rates:
                current = PREFERRED_SAMPLE_RATE
            elif current not in rates:
                current = PREFERRED_SAMPLE_RATE if PREFERRED_SAMPLE_RATE in rates else rates[0]
        self._set_combo_items(
            self.fs,
            _sample_rate_options(rates),
            current,
            styled_field=True,
        )

    def refresh_devices_for_api(self) -> None:
        self._populate_devices("in", self.selected_device_id("in"))
        self._populate_devices("out", self.selected_device_id("out"))
        self._populate_channels("in", reset=True)
        self._populate_channels("out", reset=True)
        self._populate_sample_rates(self._combo_data(self.fs), prefer_default=True)
        self.apply_audio_setup_now()

    def on_device_change(self, role: str) -> None:
        self._populate_channels(role, reset=True)
        self._populate_sample_rates(self._combo_data(self.fs), prefer_default=True)
        self.apply_audio_setup_now()

    def update_hpf_field_state(self) -> None:
        enabled = self.hpf_enable.isChecked()
        if enabled and not self.hpf.text().strip():
            self.hpf.setText(DEFAULT_PROTECTION_HPF_HZ)
        self.hpf.setEnabled(enabled)
        self.hpf_order.setEnabled(enabled)
        self.hpf_corr.setEnabled(enabled)
        cap_enabled = enabled and self.hpf_corr.isChecked()
        self.hpf_cap.setEnabled(cap_enabled)
        self._set_related_label_enabled(getattr(self.hpf, "_unit_label", None), enabled)
        self._set_related_label_enabled(self.hpf_order_label, enabled)
        self._set_related_label_enabled(self.hpf_cap_label, cap_enabled)
        self._set_related_label_enabled(getattr(self.hpf_cap, "_unit_label", None), cap_enabled)

    def _set_related_label_enabled(self, label: QLabel | None, enabled: bool) -> None:
        if label is None:
            return
        color = "#64748b" if enabled else "#94a3b8"
        label.setEnabled(enabled)
        label.setStyleSheet(f"color: {color}; font-weight: 700; border: none; padding-bottom: 2px;")

    def schedule_auto_apply(self) -> None:
        if self.auto_apply_enabled:
            self.auto_apply_timer.start()

    def apply_audio_setup_now(self, *args) -> None:
        if not self.auto_apply_enabled:
            return
        self.auto_apply_timer.stop()
        self.save_audio_setup(notify=False)

    def adjust_output_level(self, delta_db: float) -> None:
        self.level.setValue(self.level.value() + delta_db)
        self.apply_output_level_change()

    def apply_output_level_change(self) -> None:
        if not self.auto_apply_enabled:
            return
        self.level.lineEdit().deselect()
        self.auto_apply_timer.stop()
        self.update_running_sine_level()
        self.save_audio_setup(notify=False, reload_backend=False)

    def update_running_sine_level(self) -> None:
        if not self.sine_running:
            return
        self.backend.update_sine_level(self.level.value())

    def flush_pending_changes(self) -> None:
        if self.auto_apply_timer.isActive():
            self.auto_apply_timer.stop()
            self.save_audio_setup(notify=False)

    def _device_metadata(self, role: str) -> tuple[str, str]:
        dev_id = self.selected_device_id(role)
        info = self.catalog.get(dev_id, {}) if dev_id is not None else {}
        return str(info.get("name", "")), str(info.get("hostapi", ""))

    def save_audio_setup(self, notify: bool = False, reload_backend: bool = True) -> None:
        in_name, in_api = self._device_metadata("in")
        out_name, out_api = self._device_metadata("out")
        values = {
            ("audio", "in_ch_mic"): self._combo_data(self.in_mic_channel),
            ("audio", "in_ch_loop"): self._combo_data(self.in_loop_channel),
            ("audio", "out_ch_spkr"): self._combo_data(self.out_speaker_channel),
            ("audio", "out_ch_ref"): self._combo_data(self.out_ref_channel),
            ("audio", "in_dev_name"): in_name,
            ("audio", "in_dev_hostapi"): in_api,
            ("audio", "out_dev_name"): out_name,
            ("audio", "out_dev_hostapi"): out_api,
            ("audio", "fs"): self._combo_data(self.fs),
            ("audio", "blocksize"): int(self.blocksize.value()),
            ("audio", "wasapi_exclusive"): self.wasapi.isChecked(),
            ("sweep", "naming_convention"): self.naming.currentText(),
            ("sweep", "sweep_dur_s"): self.sweep_dur.value(),
            ("sweep", "sweep_level_dbfs"): self.level.value(),
            ("sweep", "num_sweeps"): int(self.num_sweeps.value()),
            ("sweep", "protect_hpf_hz"): _normalize_decimal_text(self.hpf.text()) if self.hpf_enable.isChecked() else "None",
            ("sweep", "protect_hpf_order"): int(self.hpf_order.value()),
            ("sweep", "protect_hpf_correction"): self.hpf_corr.isChecked(),
            ("sweep", "protect_hpf_corr_db_cap"): self.hpf_cap.value(),
            ("sweep", "align_to_first_marker"): self.align.isChecked(),
            ("sweep", "pre_sil_ms"): self.pre_sil.value(),
            ("sweep", "post_sil_ms"): self.post_sil.value(),
            ("sweep", "mic_tail_taper_ms"): self.taper.value(),
            ("sweep", "debug_saves"): self.debug.isChecked(),
            ("sweep", "h2_test_db"): _normalize_decimal_text(self.h2.text()),
            ("sweep", "h3_test_db"): _normalize_decimal_text(self.h3.text()),
        }
        try:
            save_config_values(
                self.config_file,
                values,
                self.backend.load if reload_backend else None,
            )
            fresh = _read_config(self.config_file)
            project.update_audio_setup(_section_dict(fresh, "audio"), _section_dict(fresh, "sweep"))
            self.saved.emit()
            if self.show_live_capture is not None:
                self.show_live_capture()
            if notify:
                QMessageBox.information(self, "Audio Setup", "Audio setup saved.")
        except Exception as exc:
            QMessageBox.warning(self, "Audio Setup", str(exc))

    def toggle_sine(self) -> None:
        if self.sine_running:
            self.backend.stop_sine()
            self.sine_running = False
            self.sine_button.setText("Play Sine")
            return
        duration_text = self.sine_duration_text.text().strip()
        duration = float(_normalize_decimal_text(duration_text)) if duration_text else None
        self.sine_running = True
        self.sine_button.setText("Stop Sine")
        worker = Worker(self.backend.play_sine, self.sine_freq.value(), self.level.value(), duration)
        if duration is not None:
            worker.signals.finished.connect(self._finish_sine_button)
        worker.signals.failed.connect(self._sine_failed)
        self.pool.start(worker)

    def _finish_sine_button(self) -> None:
        self.sine_running = False
        self.sine_button.setText("Play Sine")

    def _sine_failed(self, message: str) -> None:
        self._finish_sine_button()
        QMessageBox.warning(self, "Sine Tone", message)

    def refresh_cal_meter(self) -> None:
        state = get_audio_meter_state()
        if not state.get("active"):
            return
        inputs = state.get(str(self._combo_data(self.cal_weighting)), [])
        if len(inputs) >= 2:
            rms = inputs[1].get("rms_dbfs")
            if rms is not None:
                self.cal_meter_readings.append(float(rms))
                if len(self.cal_meter_readings) >= 5:
                    mean_power = sum(10.0 ** (value / 10.0) for value in self.cal_meter_readings) / len(self.cal_meter_readings)
                    self.held_cal_level_dbfs = 10.0 * math.log10(max(mean_power, 1e-12))
                    self.cal_meter_readings.clear()
                    self.cal_level.setText(_format_dbfs(self.held_cal_level_dbfs))

        raw_inputs = state.get("inputs", [])
        try:
            mic_index = int(self._combo_data(self.in_mic_channel))
        except (TypeError, ValueError):
            mic_index = 1
        if mic_index >= len(raw_inputs):
            return
        voltage_rms = raw_inputs[mic_index].get("rms_dbfs")
        if voltage_rms is None:
            return
        self.voltage_meter_readings.append(float(voltage_rms))
        if len(self.voltage_meter_readings) < 5:
            return
        mean_voltage_power = sum(10.0 ** (value / 10.0) for value in self.voltage_meter_readings) / len(self.voltage_meter_readings)
        self.held_voltage_input_dbfs = 10.0 * math.log10(max(mean_voltage_power, 1e-12))
        self.voltage_meter_readings.clear()
        if hasattr(self, "voltage_input_level"):
            self.voltage_input_level.setText(_format_dbfs(self.held_voltage_input_dbfs))

    def _current_output_voltage_calibration(self) -> dict[str, float] | None:
        if hasattr(self, "voltage_output_vrms") and self.voltage_output_vrms.value() > 0.0:
            calibration = {
                "output_level_dbfs": self.level.value(),
                "output_vrms": self.voltage_output_vrms.value(),
            }
            if self.voltage_amp_gain.value() != 0.0:
                calibration["amplifier_gain_db"] = self.voltage_amp_gain.value()
            return calibration
        stage5_vars = project.get_system_calibration(self.config_file)
        if not isinstance(stage5_vars, dict):
            return None
        output_cal = stage5_vars.get("output_voltage_calibration")
        if not isinstance(output_cal, dict):
            return None
        try:
            calibration = {
                "output_level_dbfs": float(_normalize_decimal_text(str(output_cal["output_level_dbfs"]))),
                "output_vrms": float(_normalize_decimal_text(str(output_cal["output_vrms"]))),
            }
            if output_cal.get("amplifier_gain_db") is not None:
                calibration["amplifier_gain_db"] = float(
                    _normalize_decimal_text(str(output_cal["amplifier_gain_db"]))
                )
        except (KeyError, TypeError, ValueError):
            return None
        return calibration

    def update_sweep_voltage_label(self) -> None:
        if not hasattr(self, "sweep_level_voltage"):
            return
        calibration = self._current_output_voltage_calibration()
        if not calibration:
            self.sweep_level_voltage.setText("")
            self.sweep_level_voltage.setVisible(False)
            return
        interface_vrms = calibration["output_vrms"] * 10.0 ** (
            (self.level.value() - calibration["output_level_dbfs"]) / 20.0
        )
        text = f"{_format_vrms(interface_vrms)} interface output"
        amp_gain = calibration.get("amplifier_gain_db")
        if amp_gain is not None:
            speaker_vrms = interface_vrms * 10.0 ** (amp_gain / 20.0)
            text += f" / {_format_vrms(speaker_vrms)} speaker input"
        self.sweep_level_voltage.setText(text)
        self.sweep_level_voltage.setVisible(True)

    def update_cal_level_label(self) -> None:
        weighting = SPL_WEIGHTING_OPTIONS.get(str(self._combo_data(self.cal_weighting)), "A")
        suffix = f"({weighting})" if weighting != "Unweighted" else " Unweighted"
        self.cal_label.setText(f"Mic RMS\ndBFS{suffix}")

    def on_cal_weighting_changed(self) -> None:
        self.cal_meter_readings.clear()
        self.held_cal_level_dbfs = None
        self.cal_level.setText(_format_dbfs(None))
        self.update_cal_level_label()

    def calculate_spl_offset(self) -> None:
        if self.held_cal_level_dbfs is None:
            QMessageBox.warning(self, "SPL Calibration", "Play the sine tone until the mic level readout appears.")
            return
        self.spl_offset.setValue(self.spl_reading.value() - self.held_cal_level_dbfs)

    def update_calibration_from_offset(self) -> bool:
        calibration = project.build_spl_calibration(
            self.spl_reading.value() if self.held_cal_level_dbfs is not None else None,
            self.held_cal_level_dbfs,
            self.spl_offset.value(),
            _spl_weighting_label(self._combo_data(self.cal_weighting)),
        )
        if calibration is None:
            return False
        calibration["input_calibration_method"] = "spl_meter"
        project.update_system_calibration(self.config_file, calibration, replace_input=True)
        return True

    def save_output_voltage_calibration(self) -> None:
        try:
            calibration = project.build_voltage_calibration(
                self.level.value(),
                self.voltage_output_vrms.value(),
                self.voltage_amp_gain.value() if self.voltage_amp_gain.value() != 0.0 else None,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Voltage Calibration", str(exc))
            return
        project.update_system_calibration(self.config_file, calibration)
        self.update_sweep_voltage_label()
        self._show_system_calibration_saved("Output voltage calibration saved.")

    def save_voltage_spl_calibration(self) -> None:
        if self.held_voltage_input_dbfs is None:
            QMessageBox.warning(self, "Voltage Calibration", "Play the loopback signal until the input level readout appears.")
            return
        try:
            calibration = project.build_voltage_calibration(
                self.level.value(),
                self.voltage_output_vrms.value(),
                self.voltage_amp_gain.value() if self.voltage_amp_gain.value() != 0.0 else None,
                self.held_voltage_input_dbfs,
                self.mic_sensitivity.value(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Voltage Calibration", str(exc))
            return
        project.update_system_calibration(self.config_file, calibration, replace_input=True)
        self._set_combo_data(self.cal_weighting, "inputs")
        self.held_cal_level_dbfs = self.held_voltage_input_dbfs
        self.spl_reading.setValue(float(calibration.get("spl_db") or 0.0))
        self.spl_offset.setValue(float(calibration.get("frd_db_offset") or 0.0))
        self.update_sweep_voltage_label()
        self._show_system_calibration_saved("Input voltage calibration saved.")

    def save_spl_calibration(self) -> None:
        if not self.update_calibration_from_offset():
            QMessageBox.warning(self, "SPL Calibration", "Enter or calculate an SPL offset first.")
            return
        self._show_system_calibration_saved("SPL meter input calibration saved.")

    def _show_system_calibration_saved(self, message: str) -> None:
        self.auto_apply_timer.stop()
        self.save_audio_setup(notify=False, reload_backend=False)
        QMessageBox.information(self, "Calibration", message)

    def test_sweep(self) -> None:
        self.save_audio_setup(notify=False)
        worker = Worker(self.backend.test_sweep)
        worker.signals.finished.connect(self._test_sweep_finished)
        worker.signals.failed.connect(lambda message: QMessageBox.warning(self, "Test Sweep", message))
        self.pool.start(worker)

    def _test_sweep_finished(self) -> None:
        if self.show_live_capture is not None:
            self.show_live_capture()
        self.saved.emit()
