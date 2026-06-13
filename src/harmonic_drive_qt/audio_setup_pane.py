"""Native Audio Setup pane."""

from __future__ import annotations

import configparser
from typing import Any

from harmonic_drive import project
from harmonic_drive.config_editor import (
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


def _optional_float_text(parser, section: str, key: str, fallback: str = "") -> str:
    raw = _value(parser, section, key, fallback)
    if raw.strip().lower() in ("", "none"):
        return ""
    return raw


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


def _format_dbfs(value: float | None) -> str:
    if value is None or value <= -119.0:
        return "-inf dBFS"
    return f"{value:.1f} dBFS"


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
        self.cal_meter_peaks: list[float] = []
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
        in_dev_id = _int_value(parser, "audio", "in_dev")
        out_dev_id = _int_value(parser, "audio", "out_dev")
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
        layout.addWidget(api_field, 0, Qt.AlignmentFlag.AlignLeft)

        device_row = QHBoxLayout()
        self.in_device = QComboBox()
        self.out_device = QComboBox()
        self.in_mic_channel = QComboBox()
        self.in_loop_channel = QComboBox()
        self.out_speaker_channel = QComboBox()
        self.out_ref_channel = QComboBox()
        device_row.addWidget(self._device_group("Input", self.in_device, self.in_mic_channel, self.in_loop_channel))
        device_row.addWidget(self._device_group("Output", self.out_device, self.out_speaker_channel, self.out_ref_channel, output=True))
        layout.addLayout(device_row)

        self._populate_devices("in", in_dev_id)
        self._populate_devices("out", out_dev_id)
        self._populate_channels("in")
        self._populate_channels("out")
        self._set_combo_data(self.in_mic_channel, _int_value(parser, "audio", "in_ch_mic"))
        self._set_combo_data(self.in_loop_channel, _int_value(parser, "audio", "in_ch_loop"))
        self._set_combo_data(self.out_speaker_channel, _int_value(parser, "audio", "out_ch_spkr"))
        self._set_combo_data(self.out_ref_channel, _int_value(parser, "audio", "out_ch_ref"))

        self.level = self._spin(_float_value(parser, "sweep", "sweep_level_dbfs", -20.0), -120, 0, "", 1)
        self.level.setFixedWidth(110)
        self.fs = QComboBox()
        self.fs.setProperty("fitToContents", True)
        self._populate_sample_rates(_int_value(parser, "audio", "fs", 48000))
        row = QHBoxLayout()
        row.addWidget(self._labeled_with_unit("Output level", self.level, "dBFS"))
        row.addWidget(self._labeled_with_unit("FS", self.fs, "Hz"))
        row.addStretch(1)
        layout.addLayout(row)

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
        layout.addWidget(self._build_sweep_group(parser))

        action_row = QHBoxLayout()
        test = QPushButton("   TEST SWEEP")
        test.setFixedSize(160, 40)
        primary_button(test)
        test.clicked.connect(self.test_sweep)
        action_row.addWidget(test)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        layout.addStretch(1)

        self._connect_auto_apply_controls()
        self.api_select.currentIndexChanged.connect(self.refresh_devices_for_api)
        self.in_device.currentIndexChanged.connect(lambda: self.on_device_change("in"))
        self.out_device.currentIndexChanged.connect(lambda: self.on_device_change("out"))

    def _device_group(self, title: str, device: QComboBox, first: QComboBox, second: QComboBox, output: bool = False) -> QFrame:
        group = QFrame()
        color = "#bfdbfe" if output else "#fbcfe8"
        fill = "#f1f7ff" if output else "#fff6fb"
        text_color = "#1e3a8a" if output else "#831843"
        group.setStyleSheet(f"QFrame {{ background: {fill}; border: 1px solid {color}; border-radius: 4px; }}")
        
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)
        
        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {text_color}; font-weight: bold; font-size: 14px; border: none;")
        layout.addWidget(lbl)
        
        def add_field(label_text, widget):
            w = QWidget()
            w.setStyleSheet(f"background: {fill}; border: 1px solid {color}; border-radius: 4px; padding: 4px;")
            l = QVBoxLayout(w)
            l.setContentsMargins(8, 2, 8, 2)
            l.setSpacing(0)
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #64748b; font-size: 10px; border: none;")
            widget.setProperty("toneFill", fill)
            self._apply_tinted_combo_style(widget)
            l.addWidget(lbl)
            l.addWidget(widget)
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

    def _build_spl_group(self) -> QGroupBox:
        group = QGroupBox("SPL Calibration")
        row = QHBoxLayout(group)
        row.setSpacing(8)
        current_calibration = project.get_project_data().get("stage5_vars")
        current_scale = current_calibration.get("frd_db_offset") if isinstance(current_calibration, dict) else None
        self.cal_level = QLabel(_format_dbfs(None))
        self.cal_level.setStyleSheet("font-family: Consolas; font-weight: 700; font-size: 15px;")
        self.spl_reading = self._spin(0.0, 0, 200, "", 1)
        self.spl_reading.setSpecialValueText("")
        self.spl_reading.setFixedWidth(112)
        self.spl_offset = self._spin(float(current_scale or 0.0), -200, 200, "", 2)
        self.spl_offset.setFixedWidth(112)
        calc = QPushButton("Calibrate")
        primary_button(calc)
        calc.setFixedSize(100, 36)
        calc.clicked.connect(self.calculate_spl_offset)
        save = QPushButton("Save Cal")
        primary_button(save)
        save.setFixedSize(100, 36)
        save.clicked.connect(self.save_spl_calibration)
        cal_box = QWidget()
        cal_box.setStyleSheet("border: 1px solid #fbcfe8; border-radius: 4px; background: #fff6fb;")
        cal_layout = QHBoxLayout(cal_box)
        cal_layout.setContentsMargins(8, 2, 8, 2)
        cal_label = QLabel("Mic Level\ndBFS(A)")
        cal_label.setStyleSheet("color: #831843; font-size: 10px; font-weight: 700;")
        cal_layout.addWidget(cal_label)
        cal_layout.addWidget(self.cal_level)
        row.addWidget(cal_box)
        row.addWidget(self._labeled_with_unit("Meter Reading", self.spl_reading, "dB SPL"))
        row.addWidget(calc)
        row.setAlignment(calc, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self._labeled_with_unit("SPL Offset", self.spl_offset, "dB"))
        row.addWidget(save)
        row.setAlignment(save, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        return group

    def _build_sweep_group(self, parser) -> QGroupBox:
        group = QGroupBox("Sweep Settings")
        layout = QVBoxLayout(group)
        grid = QGridLayout()
        self.sweep_dur = self._spin(_float_value(parser, "sweep", "sweep_dur_s", 5.0), 0.01, 3600, " s", 2)
        self.num_sweeps = self._spin(_int_value(parser, "sweep", "num_sweeps", 1), 1, 100, "", 0)
        hpf_raw = _value(parser, "sweep", "protect_hpf_hz", "None")
        self.hpf_enable = QCheckBox("Protection HPF")
        self.hpf_enable.setChecked(hpf_raw.strip().lower() not in ("", "none", "0"))
        self.hpf_enable.setStyleSheet(toggle_style())
        self.hpf = QLineEdit(_optional_float_text(parser, "sweep", "protect_hpf_hz"))
        self.hpf.setPlaceholderText("500")
        self.hpf.setFixedWidth(120)
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
        self.h2 = QLineEdit(_value(parser, "sweep", "h2_test_db", "None"))
        self.h3 = QLineEdit(_value(parser, "sweep", "h3_test_db", "None"))
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
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        original_focus = spin.focusInEvent

        def focus_in(event, spin=spin, original_focus=original_focus) -> None:
            original_focus(event)
            QTimer.singleShot(0, spin.selectAll)

        spin.focusInEvent = focus_in  # type: ignore[method-assign]
        return spin

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
                "padding: 4px 24px 4px 8px;"
                "}"
                "QComboBox:disabled {"
                "background: #f1f5f9;"
                "border-color: #d8dee8;"
                "color: #94a3b8;"
                "}"
                "QComboBox::drop-down { border: 0; width: 22px; }"
            )
            return
        widget.setStyleSheet(
            f"QComboBox {{ border: none; background: {fill}; font-size: 10pt; color: #0f172a; padding: 1px 18px 1px 0; }}"
            "QComboBox::drop-down { border: none; width: 16px; }"
        )

    def _connect_auto_apply_controls(self) -> None:
        controls = [
            self.in_mic_channel, self.in_loop_channel, self.out_speaker_channel, self.out_ref_channel,
            self.fs, self.blocksize, self.wasapi, self.level, self.sweep_dur, self.num_sweeps,
            self.hpf, self.hpf_enable, self.hpf_order, self.hpf_corr, self.hpf_cap, self.naming,
            self.align, self.debug, self.pre_sil, self.post_sil, self.taper, self.h2, self.h3,
        ]
        for control in controls:
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self.schedule_auto_apply)
            elif isinstance(control, QCheckBox):
                control.stateChanged.connect(self.schedule_auto_apply)
            elif isinstance(control, QDoubleSpinBox):
                control.valueChanged.connect(self.schedule_auto_apply)
            elif isinstance(control, QLineEdit):
                control.textChanged.connect(self.schedule_auto_apply)

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
        combo.setFixedWidth(combo.sizeHint().width())

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

    def _populate_channels(self, role: str) -> None:
        capability = "input" if role == "in" else "output"
        dev_id = self.selected_device_id(role)
        opts = _channel_options(self.catalog, dev_id, capability)
        targets = (
            (self.in_mic_channel, self.in_loop_channel)
            if role == "in"
            else (self.out_speaker_channel, self.out_ref_channel)
        )
        for combo in targets:
            current = combo.currentData()
            if current not in opts and opts:
                current = opts[0]
            self._set_combo_items(combo, opts, current)

    def _populate_sample_rates(self, current=None) -> None:
        rates = get_supported_sample_rates(self.selected_device_id("in"), self.selected_device_id("out"))
        if not rates and current:
            rates = [int(current)]
        elif rates and current not in rates:
            current = rates[0]
        self._set_combo_items(
            self.fs,
            _sample_rate_options(rates),
            current,
            styled_field=True,
        )

    def refresh_devices_for_api(self) -> None:
        self._populate_devices("in", self.selected_device_id("in"))
        self._populate_devices("out", self.selected_device_id("out"))
        self._populate_channels("in")
        self._populate_channels("out")
        self._populate_sample_rates(self._combo_data(self.fs))
        self.schedule_auto_apply()

    def on_device_change(self, role: str) -> None:
        self._populate_channels(role)
        self._populate_sample_rates(self._combo_data(self.fs))
        self.schedule_auto_apply()

    def update_hpf_field_state(self) -> None:
        enabled = self.hpf_enable.isChecked()
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

    def _device_metadata(self, role: str) -> tuple[str, str]:
        dev_id = self.selected_device_id(role)
        info = self.catalog.get(dev_id, {}) if dev_id is not None else {}
        return str(info.get("name", "")), str(info.get("hostapi", ""))

    def save_audio_setup(self, notify: bool = False) -> None:
        in_name, in_api = self._device_metadata("in")
        out_name, out_api = self._device_metadata("out")
        values = {
            ("audio", "in_dev"): self.selected_device_id("in"),
            ("audio", "out_dev"): self.selected_device_id("out"),
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
            ("sweep", "protect_hpf_hz"): self.hpf.text() if self.hpf_enable.isChecked() else "None",
            ("sweep", "protect_hpf_order"): int(self.hpf_order.value()),
            ("sweep", "protect_hpf_correction"): self.hpf_corr.isChecked(),
            ("sweep", "protect_hpf_corr_db_cap"): self.hpf_cap.value(),
            ("sweep", "align_to_first_marker"): self.align.isChecked(),
            ("sweep", "pre_sil_ms"): self.pre_sil.value(),
            ("sweep", "post_sil_ms"): self.post_sil.value(),
            ("sweep", "mic_tail_taper_ms"): self.taper.value(),
            ("sweep", "debug_saves"): self.debug.isChecked(),
            ("sweep", "h2_test_db"): self.h2.text(),
            ("sweep", "h3_test_db"): self.h3.text(),
        }
        try:
            save_config_values(self.config_file, values, self.backend.load)
            fresh = _read_config(self.config_file)
            project.update_audio_setup(_section_dict(fresh, "audio"), _section_dict(fresh, "sweep"))
            self.update_calibration_from_offset()
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
        duration = float(duration_text) if duration_text else None
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
        inputs = state.get("a_weighted_inputs", [])
        if len(inputs) < 2:
            return
        peak = inputs[1].get("peak_dbfs")
        if peak is None:
            return
        self.cal_meter_peaks.append(float(peak))
        if len(self.cal_meter_peaks) < 5:
            return
        self.held_cal_level_dbfs = max(self.cal_meter_peaks)
        self.cal_meter_peaks.clear()
        self.cal_level.setText(_format_dbfs(self.held_cal_level_dbfs))

    def calculate_spl_offset(self) -> None:
        if self.held_cal_level_dbfs is None:
            QMessageBox.warning(self, "SPL Calibration", "Play the sine tone until the mic level readout appears.")
            return
        self.spl_offset.setValue(self.spl_reading.value() - self.held_cal_level_dbfs)
        self.update_calibration_from_offset()

    def update_calibration_from_offset(self) -> bool:
        calibration = project.build_spl_calibration(None, None, self.spl_offset.value())
        if calibration is None:
            return False
        project.update_spl_calibration(calibration)
        return True

    def save_spl_calibration(self) -> None:
        if not self.update_calibration_from_offset():
            QMessageBox.warning(self, "SPL Calibration", "Enter or calculate an SPL offset first.")
            return
        project.save_project()
        QMessageBox.information(self, "SPL Calibration", "SPL calibration saved.")

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
