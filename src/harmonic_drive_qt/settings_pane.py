"""Inline Settings pane — replaces the modal SettingsDialog.

Mirrors the AudioSetupPane style: a scrollable QWidget with QGroupBox
sections and auto-apply behaviour.
"""
from __future__ import annotations

import configparser
import math
import shutil
from pathlib import Path
from typing import Callable

from .config_support import (
    DISPLAY_LABELS,
    EDITABLE_SCHEMA,
    CONFIG_EDITOR_SECTION_KEYS,
    MEASUREMENT_POINTS_TYPES,
    MOTION_MANAGER_TYPES,
    _parse_bool,
    _strip_inline_comment,
    _format_for_ini,
    _coerce,
    _serial_port_options,
)

from .qt_compat import (
    QCheckBox,
    QComboBox,
    QEvent,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QObject,
    QPushButton,
    QScrollArea,
    QTimer,
    QVBoxLayout,
    QWidget,
    Qt,
    Signal,
)
from .styles import light_combo, toggle_style

LOG_LEVEL_LABELS = {
    "TRACE": "High",
    "DEBUG": "Medium",
    "INFO": "Med-low",
    "WARNING": "Low",
    "ERROR": "Quiet",
}

DECIMAL_TEXT_KINDS = {"int", "float", "opt_float", "optional_float"}
DEFAULT_CONFIG_FILENAME = "config_default.ini"

# Units extracted from schema tooltips for display beside the value
FIELD_UNITS: dict[tuple[str, str], str] = {
    # scanner
    ("scanner", "reflection_free_time_ms"): "ms",
    ("scanner", "feed_rate"): "mm/min",
    ("scanner", "cal_tool_height"): "mm",
    # grbl_streamer
    ("grbl_streamer", "type"): "",
    ("grbl_streamer", "baudrate"): "",
    ("grbl_streamer", "mock_linear_speed_mm_s"): "mm/s",
    ("grbl_streamer", "mock_angular_speed_deg_s"): "deg/s",
    ("grbl_streamer", "mock_status_hz"): "Hz",
    # windows
    ("windows", "port"): "",
    # motion_manager
    ("motion_manager", "type"): "",
    ("motion_manager", "optimize_point_order"): "",
    ("motion_manager", "unsafe_point_policy"): "",
    # measurement points
    ("__measurement_points__", "filename"): "",
    ("__measurement_points__", "homing_gap"): "deg",
    ("__measurement_points__", "pole_gap"): "mm",
    ("__measurement_points__", "nr_of_angular_points"): "",
    ("__measurement_points__", "nr_of_radial_cap_points"): "",
    ("__measurement_points__", "nr_of_vertical_points"): "",
    ("__measurement_points__", "cap_spacing"): "mm",
    ("__measurement_points__", "wall_spacing"): "mm",
    ("__measurement_points__", "radius"): "mm",
    ("__measurement_points__", "height"): "mm",
    ("__measurement_points__", "nr_of_points"): "",
    ("__measurement_points__", "speaker_height"): "mm",
    ("__measurement_points__", "speaker_width"): "mm",
    ("__measurement_points__", "speaker_depth"): "mm",
    # app
    ("app", "show_machine_coordinate_system"): "",
    ("app", "coord_viewer_backend"): "",
    ("app", "show_rehome_button"): "",
    ("app", "show_height_offset_controls"): "",
    ("app", "auto_hide_left_menu"): "",
    ("app", "default_project_dir"): "",
    ("app", "show_motion_preview"): "",
    # logging
    ("logging", "level"): "",
    ("logging", "rotation"): "",
    ("logging", "retention"): "",
    # sweep
    ("sweep", "naming_convention"): "",
    ("sweep", "sweep_dur_s"): "s",
    ("sweep", "sweep_level_dbfs"): "dBFS",
    ("sweep", "num_sweeps"): "",
    ("sweep", "loopback_correction"): "",
    ("sweep", "protect_hpf_hz"): "Hz",
    ("sweep", "protect_hpf_order"): "",
    ("sweep", "protect_hpf_correction"): "",
    ("sweep", "protect_hpf_corr_db_cap"): "dB",
    ("sweep", "align_to_first_marker"): "",
    ("sweep", "pre_sil_ms"): "ms",
    ("sweep", "post_sil_ms"): "ms",
    ("sweep", "mic_tail_taper_ms"): "ms",
    ("sweep", "debug_saves"): "",
    ("sweep", "h2_test_db"): "dB",
    ("sweep", "h3_test_db"): "dB",
    # debug
    ("debug", "serial_comms"): "",
    ("debug", "direct_progress_ui"): "",
    ("debug", "in_app_log_viewer"): "",
    ("debug", "deep_ui_diagnostics"): "",
    # internal UI keys
    ("__motion_manager_ui__", "measurement_points_section"): "",
    ("__motion_manager_ui__", "measurement_points_type"): "",
}

SCANNER_RELATED_SECTIONS = {"scanner", "grbl_streamer", "windows", "motion_manager"}


def _normalize_decimal_editor_text(widget: QLineEdit, text: str) -> None:
    if "," not in text:
        return
    cursor_pos = widget.cursorPosition()
    widget.setText(text.replace(",", "."))
    widget.setCursorPosition(cursor_pos)


class _ComboWheelFilter(QObject):
    """Prevent combo boxes from stealing wheel scroll inside a scroll area."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return super().eventFilter(obj, event)


class SettingsPane(QWidget):
    """Inline settings panel that auto-applies changes."""

    saved = Signal()

    def __init__(
        self,
        backend,
        config_file: str,
        on_apply: Callable[[set[str]], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.backend = backend
        self.config_file = config_file
        self.on_apply = on_apply
        self.parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        self.parser.optionxform = str  # type: ignore[assignment]
        self._combo_wheel_filter = _ComboWheelFilter(self)

        # Load config
        self._reload_config_state()
        self._initial_config = self._snapshot_config()

        # Ensure RFT default is loaded
        if self.parser.has_option("scanner", "reflection_free_time_ms"):
            from . import project

            project.get_system_rft_default(config_file)
            self.parser.read(config_file)

        self.auto_apply_enabled = False
        self._build_ui()
        self.auto_apply_enabled = True

        # Auto-apply timer (debounced save)
        self.auto_apply_timer = QTimer(self)
        self.auto_apply_timer.setSingleShot(True)
        self.auto_apply_timer.setInterval(800)
        self.auto_apply_timer.timeout.connect(self._auto_apply)

    # ------------------------------------------------------------------
    # Config state helpers
    # ------------------------------------------------------------------

    def _reload_config_state(self) -> None:
        self.parser.clear()
        self.parser.read(self.config_file)
        self.inputs: dict[tuple[str, str], tuple[QWidget, str]] = {}
        self.motion_manager_extra_keys = {
            key
            for entries in MOTION_MANAGER_TYPES.values()
            for key, _kind, _tooltip, _options in entries
        }
        self.motion_manager_extra_widgets: dict[str, QWidget] = {}
        self.motion_manager_mp_section_name = _strip_inline_comment(
            self.parser.get("motion_manager", "measurement_points", fallback="")
        )
        self.measurement_points_extra_keys = {
            key
            for entries in MEASUREMENT_POINTS_TYPES.values()
            for key, _kind, _tooltip, _options in entries
        }
        self.measurement_points_extra_widgets: dict[str, QWidget] = {}
        self.measurement_points_section_input: QWidget | None = None
        self.measurement_points_type_input: QWidget | None = None

    def _snapshot_config(self) -> dict[str, dict[str, str]]:
        snapshot: dict[str, dict[str, str]] = {}
        for section in self.parser.sections():
            snapshot[section] = {
                key: self.parser.get(section, key) for key in self.parser.options(section)
            }
        return snapshot

    def _changed_sections(self) -> set[str]:
        current = self._snapshot_config()
        changed: set[str] = set()
        all_sections = set(self._initial_config.keys()) | set(current.keys())
        for section in all_sections:
            old_vals = self._initial_config.get(section, {})
            new_vals = current.get(section, {})
            if old_vals != new_vals:
                changed.add(section)
        return changed

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet("background-color: #ffffff;")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(6, 0, 12, 18)
        content_layout.setSpacing(0)
        scroll.setWidget(content)
        root.addWidget(scroll)

        # Title
        title = QLabel("Settings")
        title.setStyleSheet("font-size: 20px; font-weight: 800; color: #000000;")
        content_layout.addWidget(title)

        # Restore defaults button (top-right area)
        restore_row = QHBoxLayout()
        restore_row.addStretch(1)
        self.restore_btn = QPushButton("RESTORE DEFAULTS")
        self.restore_btn.setStyleSheet(
            "color: #dc2626; font-weight: bold; padding: 6px 14px; "
            "border: 1px solid #fca5a5; border-radius: 4px; background: white;"
        )
        self.restore_btn.clicked.connect(self.restore_defaults)
        restore_row.addWidget(self.restore_btn)
        content_layout.addLayout(restore_row)

        # Single column of sections, each with two columns of fields
        if not self.parser.has_section("app"):
            self.parser.add_section("app")

        sections_layout = QVBoxLayout()
        sections_layout.setContentsMargins(0, 14, 0, 0)
        sections_layout.setSpacing(14)
        content_layout.addLayout(sections_layout)

        # Initialize arduino-only widget tracking
        self._arduino_only_widgets: list[QWidget] = []
        self._grbl_type_combo: QComboBox | None = None

        # Scanner section
        scanner_group = self._build_scanner_group()
        sections_layout.addWidget(scanner_group)

        # Motion Manager section
        motion_group = self._build_motion_manager_group()
        sections_layout.addWidget(motion_group)

        # App section
        app_group = self._build_section_group("App", "app")
        sections_layout.addWidget(app_group)

        # Logging section
        logging_group = self._build_section_group("Logging", "logging")
        sections_layout.addWidget(logging_group)

        # Sweep section
        if self.parser.has_section("sweep"):
            sweep_group = self._build_section_group("Sweep", "sweep")
            sections_layout.addWidget(sweep_group)

        # GRBL connection section
        self._grbl_connection_group = self._build_grbl_connection_group()
        sections_layout.addWidget(self._grbl_connection_group)

        # Update visibility based on current GRBL type
        self._update_grbl_visibility()

    @staticmethod
    def _build_group(title: str, checkable: bool = False, checked: bool = True) -> tuple[QGroupBox, QGridLayout]:
        group = QGroupBox(title)
        if checkable:
            group.setCheckable(True)
            group.setChecked(checked)
        group_layout = QGridLayout(group)
        group_layout.setContentsMargins(10, 8, 10, 8)
        group_layout.setSpacing(8)
        group_layout.setHorizontalSpacing(16)
        group_layout.setColumnStretch(0, 1)
        group_layout.setColumnStretch(1, 1)
        return group, group_layout

    def _build_scanner_group(self) -> QGroupBox:
        """Build the Scanner section (without GRBL connection)."""
        group, group_layout = SettingsPane._build_group("Scanner")
        self._build_fields(group_layout, "scanner", EDITABLE_SCHEMA.get("scanner", []))
        return group

    def _build_grbl_connection_group(self) -> QGroupBox:
        """Build the GRBL connection section with conditional visibility."""
        group, group_layout = SettingsPane._build_group("GRBL connection")

        grbl_type_input = None
        self._mock_dro_rows: list[QWidget] = []
        self._arduino_rows: list[QWidget] = []

        row = 0
        col = 0
        for grbl_sec, fld_key, label in (
            ("grbl_streamer", "type", "Grbl Streamer Type"),
            ("grbl_streamer", "baudrate", "Baudrate"),
            ("windows", "port", "Com Port"),
            ("grbl_streamer", "mock_linear_speed_mm_s", "Mock linear speed"),
            ("grbl_streamer", "mock_angular_speed_deg_s", "Mock angular speed"),
            ("grbl_streamer", "mock_status_hz", "Mock DRO update rate"),
            ("debug", "serial_comms", "Log serial comms on startup"),
        ):
            if not self.parser.has_section(grbl_sec):
                continue
            entry = next((e for e in EDITABLE_SCHEMA.get(grbl_sec, []) if e[0] == fld_key), None)
            if entry is None:
                continue
            raw = _strip_inline_comment(self.parser.get(grbl_sec, fld_key, fallback=""))
            options = entry[3]
            if grbl_sec == "windows" and fld_key == "port":
                port_opts = _serial_port_options(raw)
                options = list(port_opts.keys())
            field, row_widget = self._add_field_grid(
                group_layout, grbl_sec, fld_key, label, entry[1], raw, options, row, col
            )
            if grbl_sec == "grbl_streamer" and fld_key == "type":
                grbl_type_input = field
                self._grbl_type_combo = field if isinstance(field, QComboBox) else None
            elif fld_key in {"mock_linear_speed_mm_s", "mock_angular_speed_deg_s", "mock_status_hz"}:
                self._mock_dro_rows.append(row_widget)
            elif fld_key in {"baudrate", "port", "serial_comms"}:
                self._arduino_rows.append(row_widget)
            col += 1
            if col == 2:
                col = 0
                row += 1

        if isinstance(grbl_type_input, QComboBox):
            grbl_type_input.currentTextChanged.connect(self._update_grbl_visibility)

        return group

    def _update_grbl_visibility(self) -> None:
        """Update visibility of GRBL-type-dependent widgets."""
        if self._grbl_type_combo is None:
            return
        streamer_type = self._grbl_type_combo.currentText()
        is_mock = streamer_type in {"MockSimulatedDRO", "MockWithDRO"}
        is_arduino = streamer_type == "Arduino"
        for rw in self._mock_dro_rows:
            rw.setVisible(is_mock)
        for rw in self._arduino_rows:
            rw.setVisible(is_arduino)
        for w in self._arduino_only_widgets:
            w.setVisible(is_arduino)

    def _build_motion_manager_group(self) -> QGroupBox:
        """Build the Motion Manager section (returns QGroupBox)."""
        group, group_layout = SettingsPane._build_group("Motion Manager")
        self._build_motion_manager_fields(group_layout)
        return group

    def _build_section_group(self, title: str, section: str) -> QGroupBox:
        """Build a simple section group (returns QGroupBox)."""
        entries = EDITABLE_SCHEMA.get(section, [])
        if not entries:
            group, _ = SettingsPane._build_group(title)
            group.hide()
            return group
        group, group_layout = SettingsPane._build_group(title)
        self._build_fields(group_layout, section, entries)
        return group

    def _build_fields(self, layout: QGridLayout, section: str, schema: list) -> None:
        visible_keys = CONFIG_EDITOR_SECTION_KEYS.get(section)
        row = 0
        col = 0
        for key, kind, tooltip, options in schema:
            if section == "app" and key == "use_alternative_motion_controls":
                continue
            if visible_keys is not None and key not in visible_keys:
                continue
            is_rft = section == "scanner" and key == "reflection_free_time_ms"
            is_preview = section == "app" and key == "show_motion_preview"
            if not self.parser.has_option(section, key) and not (is_rft or is_preview):
                continue
            fallback = "True" if is_preview else "0"
            raw = _strip_inline_comment(self.parser.get(section, key, fallback=fallback))
            label = DISPLAY_LABELS.get(key, key.replace("_", " ").capitalize())
            field, row_widget = self._add_field_grid(layout, section, key, label, kind, raw, options, row, col)
            if is_rft:
                self._add_rft_buttons(row_widget, field)
            col += 1
            if col == 2:
                col = 0
                row += 1

    def _add_field_grid(self, layout: QGridLayout, section, key, label, kind, raw, options, row: int, col: int):
        """Create a field and add it to a QGridLayout at the given position.
        
        Matches AudioSetupPane style: description label above, value in a bordered box,
        unit beside the value.
        """
        # Get unit for this field
        unit = FIELD_UNITS.get((section, key), "")
        
        # Outer wrapper: label on top, value row below
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(2)

        # Description label above (larger font, outside the box)
        desc_label = QLabel(label)
        desc_label.setStyleSheet("color: #334155; font-size: 10pt; border: none;")
        desc_label.setFixedHeight(14)
        wrapper_layout.addWidget(desc_label)

        # Value row: bordered box with widget, unit beside it
        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(5)

        # The bordered box containing the editable widget
        box = QWidget()
        box.setStyleSheet("border: 1px solid #cbd5e1; border-radius: 4px; background: white;")
        box_layout = QHBoxLayout(box)
        box_layout.setContentsMargins(8, 4, 8, 4)
        box_layout.setSpacing(0)

        if kind == "bool":
            widget = QCheckBox()
            widget.setChecked(_parse_bool(raw))
            widget.setStyleSheet(toggle_style())
            widget.stateChanged.connect(lambda: self._schedule_auto_apply())
            box_layout.addWidget(widget)
            box_layout.addStretch(1)
        elif kind == "choice" or options:
            widget = QComboBox()
            light_combo(widget)
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            widget.installEventFilter(self._combo_wheel_filter)
            if section == "logging" and key == "level":
                for value in options or []:
                    widget.addItem(LOG_LEVEL_LABELS.get(value, value), value)
                index = widget.findData(raw)
                if index >= 0:
                    widget.setCurrentIndex(index)
            else:
                widget.addItems(options or [])
                widget.setCurrentText(raw)
            widget.currentTextChanged.connect(lambda: self._schedule_auto_apply())
            widget.setStyleSheet(
                "QComboBox { border: none; background: transparent; "
                "font-size: 9.75pt; color: #0f172a; min-height: 22px; padding: 0; }"
            )
            box_layout.addWidget(widget, 1)
        else:
            widget = QLineEdit(raw)
            if kind in DECIMAL_TEXT_KINDS:
                widget.textEdited.connect(
                    lambda text, w=widget: _normalize_decimal_editor_text(w, text)
                )
            widget.textChanged.connect(lambda: self._schedule_auto_apply())
            widget.setStyleSheet(
                "border: none; background: transparent; "
                "font-size: 9.75pt; color: #0f172a; min-height: 22px; padding: 0;"
            )
            box_layout.addWidget(widget, 1)

        value_row.addWidget(box, 1)
        
        # Unit label beside the value (outside the editable area)
        if unit:
            unit_label = QLabel(unit)
            unit_label.setStyleSheet("color: #64748b; font-weight: 700; border: none; padding-bottom: 2px;")
            value_row.addWidget(unit_label, 0, Qt.AlignmentFlag.AlignBottom)

        wrapper_layout.addLayout(value_row)
        layout.addWidget(wrapper, row, col)

        self.inputs[(section, key)] = (widget, kind)
        return widget, wrapper

    def _add_rft_buttons(self, wrapper: QWidget, field: QWidget) -> None:
        if not isinstance(field, QLineEdit):
            return
        button = QPushButton("RFT Calculator")
        button.clicked.connect(self._open_rft_calculator)
        button.setStyleSheet(
            "QPushButton { color: #2563eb; background: white; "
            "border: 1px solid #cbd5e1; border-radius: 4px; "
            "padding: 4px 10px; font-weight: bold; }"
            "QPushButton:hover { background: #eff6ff; }"
        )
        default_button = QPushButton("Set as system default")
        default_button.setToolTip("Use this RFT for new projects and future app starts.")
        default_button.setStyleSheet(button.styleSheet())
        default_button.clicked.connect(self._set_rft_system_default)

        # Navigate to the value_row (index 1 in wrapper's QVBoxLayout)
        wrapper_layout = wrapper.layout()
        if wrapper_layout is None:
            return
        
        # Find the value_row QHBoxLayout (second item in wrapper_layout)
        value_row_item = wrapper_layout.itemAt(1)
        if value_row_item is None:
            return
        
        # value_row_item is a QLayoutItem, we need to get the QHBoxLayout
        # We'll replace the value_row with a new one that includes buttons
        value_row = value_row_item.layout()
        if value_row is None:
            return
        
        # Find the box widget (first item in value_row)
        box_item = value_row.itemAt(0)
        if box_item is None:
            return
        box = box_item.widget()
        if box is None:
            return
        
        # Find the unit label (second item in value_row, if present)
        unit_label = None
        unit_item = value_row.itemAt(1)
        if unit_item is not None:
            w = unit_item.widget()
            if isinstance(w, QLabel):
                unit_label = w
        
        # Remove old items from value_row
        value_row.removeWidget(box)
        if unit_label is not None:
            value_row.removeWidget(unit_label)
        box.deleteLater()
        if unit_label is not None:
            unit_label.deleteLater()
        
        # Create new inline layout: [field] [unit] [buttons]
        inline = QHBoxLayout()
        inline.setContentsMargins(0, 0, 0, 0)
        inline.setSpacing(5)
        
        # Create a new box for the field
        new_box = QWidget()
        new_box.setStyleSheet("border: 1px solid #cbd5e1; border-radius: 4px; background: white;")
        new_box_layout = QHBoxLayout(new_box)
        new_box_layout.setContentsMargins(8, 4, 8, 4)
        new_box_layout.setSpacing(0)
        new_box_layout.addWidget(field, 1)
        
        inline.addWidget(new_box, 1)
        
        # Add unit label if present
        unit_text = FIELD_UNITS.get(("scanner", "reflection_free_time_ms"), "")
        if unit_text:
            unit_lbl = QLabel(unit_text)
            unit_lbl.setStyleSheet("color: #64748b; font-weight: 700; border: none; padding-bottom: 2px;")
            inline.addWidget(unit_lbl, 0, Qt.AlignmentFlag.AlignBottom)
        
        inline.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        inline.addWidget(default_button, 0, Qt.AlignmentFlag.AlignVCenter)
        
        value_row.addLayout(inline)

    def _set_rft_system_default(self) -> None:
        from . import project

        field_widget, _kind = self.inputs.get(("scanner", "reflection_free_time_ms"), (None, None))
        if not isinstance(field_widget, QLineEdit):
            return
        try:
            value = float(field_widget.text())
            project.set_system_rft_default(self.config_file, value)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Error", f"Could not set RFT default: {exc}")
            return
        self.parser.set("scanner", "default_reflection_free_time_ms", str(value))
        self._schedule_auto_apply()

    def _open_rft_calculator(self) -> None:
        from rft_calc.dialog import RFTDialog
        from .qt_compat import QDialog

        dialog = RFTDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            field_widget, _kind = self.inputs.get(("scanner", "reflection_free_time_ms"), (None, None))
            if isinstance(field_widget, QLineEdit):
                field_widget.setText(f'{dialog.result["rft_ms"]:.4f}')  # type: ignore[index]
                self._schedule_auto_apply()

    # ------------------------------------------------------------------
    # Motion Manager fields
    # ------------------------------------------------------------------

    def _build_motion_manager_fields(self, layout: QGridLayout) -> None:
        row = 0
        col = 0

        raw_type = _strip_inline_comment(
            self.parser.get("motion_manager", "type", fallback="FastCylindricalMeasurementMotionManager")
        )
        type_widget, _type_row = self._add_field_grid(
            layout,
            "motion_manager",
            "type",
            "Motion Manager Type",
            "choice",
            raw_type or "FastCylindricalMeasurementMotionManager",
            ["FastCylindricalMeasurementMotionManager"],
            row, col,
        )
        if isinstance(type_widget, QComboBox):
            type_widget.setEnabled(False)
        col += 1
        if col == 2:
            col = 0
            row += 1

        for key, kind, tooltip, options in self._all_motion_manager_fields():
            raw = _strip_inline_comment(self.parser.get("motion_manager", key, fallback=""))
            label = DISPLAY_LABELS.get(key, key.replace("_", " ").title())
            _widget, rw = self._add_field_grid(
                layout, "motion_manager", key, label, kind, raw, options, row, col
            )
            self.motion_manager_extra_widgets[key] = rw
            col += 1
            if col == 2:
                col = 0
                row += 1

        def update_extra_visibility(manager_type: str) -> None:
            active_keys = {
                key
                for key, _kind, _tooltip, _options in MOTION_MANAGER_TYPES.get(manager_type, [])
            }
            for key, rw in self.motion_manager_extra_widgets.items():
                rw.setVisible(key in active_keys)

        if isinstance(type_widget, QComboBox):
            type_widget.currentTextChanged.connect(update_extra_visibility)
            update_extra_visibility(type_widget.currentText())

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("color: #e2e8f0; margin: 8px 0;")
        layout.addWidget(separator, row, 0, 1, 2)
        row += 1

        # Measurement Points sub-section
        lbl = QLabel("Measurement Points")
        lbl.setStyleSheet("font-weight: bold; font-size: 10.5pt;")
        layout.addWidget(lbl, row, 0, 1, 2)
        row += 1
        self._build_measurement_points_fields(layout, row, 0)

    def _build_measurement_points_fields(self, layout: QGridLayout, start_row: int, start_col: int) -> None:
        mp_section_name = self.motion_manager_mp_section_name
        current_type = ""
        if mp_section_name and self.parser.has_section(mp_section_name):
            current_type = _strip_inline_comment(
                self.parser.get(mp_section_name, "type", fallback="")
            )
        else:
            current_type = _strip_inline_comment(
                self.parser.get("motion_manager", "measurement_points_type", fallback="")
            )
            if not current_type:
                fallback_type = _strip_inline_comment(
                    self.parser.get("motion_manager", "type", fallback="")
                )
                if fallback_type in MEASUREMENT_POINTS_TYPES:
                    current_type = fallback_type

        row = start_row
        col = start_col

        self.measurement_points_section_input, _section_row = self._add_field_grid(
            layout,
            "__motion_manager_ui__",
            "measurement_points_section",
            "Measurement Points Section",
            "str",
            mp_section_name,
            None,
            row, col,
        )
        col += 1
        if col == 2:
            col = 0
            row += 1

        self.measurement_points_type_input, _type_row = self._add_field_grid(
            layout,
            "__motion_manager_ui__",
            "measurement_points_type",
            "Measurement Points Type",
            "choice",
            current_type or "FileMeasurementPoints",
            ["FileMeasurementPoints"],
            row, col,
        )
        if isinstance(self.measurement_points_type_input, QComboBox):
            self.measurement_points_type_input.setEnabled(False)
        col += 1
        if col == 2:
            col = 0
            row += 1

        for key, kind, tooltip, options in self._all_measurement_points_fields():
            raw = self._measurement_points_raw_value(mp_section_name, key)
            label = DISPLAY_LABELS.get(key, key.replace("_", " ").title())
            _widget, rw = self._add_field_grid(
                layout, "__measurement_points__", key, label, kind, raw, options, row, col
            )
            self.measurement_points_extra_widgets[key] = rw
            col += 1
            if col == 2:
                col = 0
                row += 1

        def update_measurement_points_fields(*_args) -> None:
            mp_type_widget = self.measurement_points_type_input
            if mp_type_widget is None:
                return
            mp_type = self._combo_or_text(mp_type_widget)
            section_name = self._line_text(self.measurement_points_section_input)
            active_keys = {
                key
                for key, _kind, _tooltip, _options in MEASUREMENT_POINTS_TYPES.get(mp_type, [])
            }
            for key, rw in self.measurement_points_extra_widgets.items():
                rw.setVisible(key in active_keys)
                if key in active_keys:
                    widget, _kind = self.inputs.get(("__measurement_points__", key), (None, None))
                    if widget is not None:
                        self._set_widget_text(widget, self._measurement_points_raw_value(section_name, key))

        if isinstance(self.measurement_points_type_input, QComboBox):
            self.measurement_points_type_input.currentTextChanged.connect(
                update_measurement_points_fields
            )
        if hasattr(self.measurement_points_section_input, "textChanged"):
            self.measurement_points_section_input.textChanged.connect(  # type: ignore[union-attr]
                update_measurement_points_fields
            )
        update_measurement_points_fields()

    # ------------------------------------------------------------------
    # Iterators
    # ------------------------------------------------------------------

    @staticmethod
    def _all_motion_manager_fields():
        seen = set()
        for entries in MOTION_MANAGER_TYPES.values():
            for entry in entries:
                key = entry[0]
                if key in seen:
                    continue
                seen.add(key)
                yield entry

    @staticmethod
    def _all_measurement_points_fields():
        seen = set()
        for entries in MEASUREMENT_POINTS_TYPES.values():
            for entry in entries:
                key = entry[0]
                if key in seen:
                    continue
                seen.add(key)
                yield entry

    # ------------------------------------------------------------------
    # Value helpers
    # ------------------------------------------------------------------

    def _measurement_points_raw_value(self, section_name: str, key: str) -> str:
        target_section = section_name or "motion_manager"
        if not self.parser.has_section(target_section):
            return ""
        return _strip_inline_comment(self.parser.get(target_section, key, fallback=""))

    def _combo_or_text(self, widget: QWidget) -> str:
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            return str(data) if data is not None else widget.currentText()
        return self._line_text(widget)

    def _line_text(self, widget) -> str:
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        return ""

    def _set_widget_text(self, widget: QWidget, raw: str) -> None:
        if isinstance(widget, QComboBox):
            if widget.currentText() != raw:
                widget.setCurrentText(raw)
        elif isinstance(widget, QLineEdit) and widget.text() != raw:
            widget.setText(raw)

    # ------------------------------------------------------------------
    # Auto-apply & save
    # ------------------------------------------------------------------

    def _schedule_auto_apply(self) -> None:
        if not self.auto_apply_enabled:
            return
        self.auto_apply_timer.start()

    def _auto_apply(self) -> None:
        if self._save_config():
            self.saved.emit()

    def flush_pending_changes(self) -> None:
        """Force-save any pending changes (called before critical operations)."""
        self.auto_apply_timer.stop()
        if self.auto_apply_enabled:
            self._auto_apply()

    def _save_config(self) -> bool:
        """Collect all widget values and write config.ini. Returns True on success."""
        selected_motion_manager_type = None
        motion_manager_widget = self.inputs.get(("motion_manager", "type"))
        if motion_manager_widget is not None:
            widget, _kind = motion_manager_widget
            if isinstance(widget, QComboBox):
                data = widget.currentData()
                selected_motion_manager_type = str(data) if data is not None else widget.currentText()
            else:
                selected_motion_manager_type = None

        active_motion_manager_keys = set()
        if selected_motion_manager_type:
            active_motion_manager_keys = {
                key
                for key, _kind, _tooltip, _options in MOTION_MANAGER_TYPES.get(
                    selected_motion_manager_type, []
                )
            }

        for (section, key), (widget, kind) in self.inputs.items():
            if section in ("__motion_manager_ui__", "__measurement_points__"):
                continue
            raw = ""
            if isinstance(widget, QCheckBox):
                raw = str(widget.isChecked())
            elif isinstance(widget, QComboBox):
                data = widget.currentData()
                raw = str(data) if data is not None else widget.currentText()
            elif isinstance(widget, QLineEdit):
                raw = widget.text()

            try:
                typed = _coerce(kind, raw)
                if section == "scanner" and key == "reflection_free_time_ms":
                    if not math.isfinite(typed) or typed < 0:
                        raise ValueError("Reflection Free Time must be finite and non-negative")
                if kind == "optional_float" and typed is None:
                    if self.parser.has_option(section, key):
                        self.parser.remove_option(section, key)
                    continue
                self.parser.set(section, key, _format_for_ini(kind, typed))
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Invalid value for {key}: {e}")
                return False

        if self.parser.has_section("motion_manager"):
            for key in self.motion_manager_extra_keys - active_motion_manager_keys:
                if self.parser.has_option("motion_manager", key):
                    self.parser.remove_option("motion_manager", key)

        if not self._save_measurement_points_fields():
            return False

        with open(self.config_file, "w") as f:
            self.parser.write(f)

        from . import project

        project.sync_rft_from_config(self.config_file)
        changed = self._changed_sections()
        self._initial_config = self._snapshot_config()
        if changed:
            self.on_apply(changed)
        return True

    def _save_measurement_points_fields(self) -> bool:
        if self.measurement_points_type_input is None:
            return True

        mp_type = self._combo_or_text(self.measurement_points_type_input).strip()
        if not mp_type:
            QMessageBox.warning(self, "Error", "Measurement points type must be set")
            return False

        new_mp_section = self._line_text(self.measurement_points_section_input)
        old_mp_section = self.motion_manager_mp_section_name

        if old_mp_section and old_mp_section != new_mp_section and self.parser.has_section(old_mp_section):
            self.parser.remove_section(old_mp_section)

        if new_mp_section:
            self.parser.set("motion_manager", "measurement_points", new_mp_section)
            if not self.parser.has_section(new_mp_section):
                self.parser.add_section(new_mp_section)
            target_section = new_mp_section
            type_key = "type"
        else:
            if self.parser.has_option("motion_manager", "measurement_points"):
                self.parser.remove_option("motion_manager", "measurement_points")
            target_section = "motion_manager"
            type_key = "measurement_points_type"

        self.parser.set(target_section, type_key, mp_type)

        active_keys = {
            key
            for key, _kind, _tooltip, _options in MEASUREMENT_POINTS_TYPES.get(mp_type, [])
        }
        for key in active_keys:
            inp = self.inputs.get(("__measurement_points__", key))
            if inp is None:
                continue
            widget, kind = inp
            if widget is None or kind is None:
                continue
            if isinstance(widget, QComboBox):
                raw = self._combo_or_text(widget)
            elif isinstance(widget, QLineEdit):
                raw = widget.text()
            else:
                continue
            if not raw:
                continue
            try:
                typed = _coerce(kind, raw)
                if kind == "optional_float" and typed is None:
                    if self.parser.has_option(target_section, key):
                        self.parser.remove_option(target_section, key)
                    continue
                self.parser.set(target_section, key, _format_for_ini(kind, typed))
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Invalid value for {key}: {e}")
                return False

        known_base_keys = {"type", "measurement_points_type"}
        for stale_key in (known_base_keys | self.measurement_points_extra_keys) - (
            known_base_keys | active_keys
        ):
            if target_section == "motion_manager" and stale_key in {
                "type",
                "unsafe_point_policy",
                "measurement_points",
                "measurement_points_type",
            }:
                continue
            if self.parser.has_option(target_section, stale_key):
                self.parser.remove_option(target_section, stale_key)

        return True

    # ------------------------------------------------------------------
    # Restore defaults
    # ------------------------------------------------------------------

    def _default_config_path(self) -> Path:
        config_path = Path(self.config_file)
        candidates = [
            config_path.with_name(DEFAULT_CONFIG_FILENAME),
            Path(DEFAULT_CONFIG_FILENAME),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def restore_defaults(self) -> None:
        default_path = self._default_config_path()
        if not default_path.exists():
            QMessageBox.warning(
                self,
                "Restore Defaults",
                f"Default config not found:\n\n{default_path}",
            )
            return

        result = QMessageBox.question(
            self,
            "Restore Defaults",
            "Replace the current configuration with config_default.ini?\n\n"
            "A .old backup will be saved first.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if result != QMessageBox.StandardButton.Yes:
            return

        config_path = Path(self.config_file)
        backup_path = config_path.with_suffix(".old")
        try:
            if config_path.exists():
                shutil.copy2(config_path, backup_path)
            shutil.copy2(default_path, config_path)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Restore Defaults",
                f"Could not restore default config:\n\n{exc}",
            )
            return

        self.on_apply(SCANNER_RELATED_SECTIONS | {"audio", "sweep", "logging", "app", "debug"})
        self._reload_config_state()
        self._initial_config = self._snapshot_config()

        # Rebuild UI to reflect restored values
        self._rebuild_ui()

    def _rebuild_ui(self) -> None:
        """Rebuild the entire pane after restore defaults."""
        root = self.layout()
        if root is None:
            return
        while root.count():
            child = root.takeAt(0)
            if child is not None and child.widget() is not None:
                w = child.widget()
                if w is not None:
                    w.deleteLater()
        self._build_ui()
