from __future__ import annotations

import configparser
import shutil
from pathlib import Path
from typing import Callable

from .config_support import (
    DISPLAY_LABELS,
    EDITABLE_SCHEMA,
    CONFIG_EDITOR_SECTION_KEYS,
    CONFIG_EDITOR_HIDDEN_SECTIONS,
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
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    Qt,
)
from .styles import light_combo, primary_button, toggle_style

LOG_LEVEL_LABELS = {
    "TRACE": "High",
    "DEBUG": "Medium",
    "INFO": "Med-low",
    "WARNING": "Low",
    "ERROR": "Quiet",
}

DECIMAL_TEXT_KINDS = {"int", "float", "opt_float", "optional_float"}
DEFAULT_CONFIG_FILENAME = "config_default.ini"


def _normalize_decimal_editor_text(widget: QLineEdit, text: str) -> None:
    if "," not in text:
        return
    cursor_pos = widget.cursorPosition()
    widget.setText(text.replace(",", "."))
    widget.setCursorPosition(cursor_pos)


class SettingsDialog(QDialog):
    def __init__(self, config_file: str, on_apply: Callable[[], None], parent=None):
        super().__init__(parent)
        self.config_file = config_file
        self.on_apply = on_apply
        self.parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        self.parser.optionxform = str
        self._reload_config_state()
        
        self.setWindowTitle("Edit configuration")
        self.resize(760, 560)
        self.setMinimumSize(560, 360)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("background-color: #ffffff;")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(8)
        
        title = QLabel("Edit configuration")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #0f172a; border: none;")
        subtitle = QLabel("config.ini")
        subtitle.setStyleSheet("color: #64748b; font-size: 12px; border: none; margin-bottom: 8px;")
        
        layout.addWidget(title)
        layout.addWidget(subtitle)
        
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #e2e8f0; border-radius: 4px; top: -1px; } "
            "QTabBar::tab { background: transparent; color: #475569; padding: 7px 14px; font-weight: bold; } "
            "QTabBar::tab:selected { color: #0f172a; border-bottom: 2px solid #0f172a; }"
        )
        layout.addWidget(self.tabs)
        
        self._populate_tabs()
            
        btn_layout = QHBoxLayout()
        restore = QPushButton("RESTORE DEFAULTS")
        restore.setStyleSheet("color: #dc2626; font-weight: bold; padding: 8px 16px; border: 1px solid #fca5a5; border-radius: 4px; background: white;")
        restore.clicked.connect(self.restore_defaults)
        
        cancel = QPushButton("CANCEL")
        cancel.setStyleSheet("color: #3b82f6; font-weight: bold; padding: 8px 16px; border: none; background: transparent;")
        cancel.clicked.connect(self.reject)
        
        ok = QPushButton("OK")
        primary_button(ok)
        ok.clicked.connect(self.save)
        
        btn_layout.addWidget(restore)
        btn_layout.addStretch(1)
        btn_layout.addWidget(cancel)
        btn_layout.addWidget(ok)
        layout.addLayout(btn_layout)

    def _reload_config_state(self) -> None:
        self.parser.clear()
        self.parser.read(self.config_file)
        self.inputs = {}
        self.tab_sections: dict[str, int] = {}
        self.motion_manager_extra_keys = {
            key
            for entries in MOTION_MANAGER_TYPES.values()
            for key, _kind, _tooltip, _options in entries
        }
        self.motion_manager_extra_widgets = {}
        self.motion_manager_mp_section_name = _strip_inline_comment(
            self.parser.get("motion_manager", "measurement_points", fallback="")
        )
        self.measurement_points_extra_keys = {
            key
            for entries in MEASUREMENT_POINTS_TYPES.values()
            for key, _kind, _tooltip, _options in entries
        }
        self.measurement_points_extra_widgets = {}
        self.measurement_points_section_input = None
        self.measurement_points_type_input = None

    def _populate_tabs(self) -> None:
        self.tabs.clear()
        for section in EDITABLE_SCHEMA:
            if self.parser.has_section(section) and section not in CONFIG_EDITOR_HIDDEN_SECTIONS:
                self._add_tab(section)

        if self.parser.has_section("motion_manager"):
            self._add_tab("motion_manager")

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

        self.on_apply()
        self._reload_config_state()
        self._populate_tabs()

    def _add_tab(self, section: str) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame if hasattr(QFrame, "Shape") else 0)
        
        w = QWidget()
        w.setStyleSheet("background: white;")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(7)
        
        if section == "scanner":
            lbl = QLabel("Scanner")
            lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
            layout.addWidget(lbl)
            self._build_fields(layout, "scanner", EDITABLE_SCHEMA["scanner"])
            
            lbl2 = QLabel("GRBL connection")
            lbl2.setStyleSheet("font-weight: bold; font-size: 14px; margin-top: 16px;")
            layout.addWidget(lbl2)
            
            grbl_type_input = None
            mock_dro_rows = []
            for grbl_sec, key, label in (
                ("grbl_streamer", "type", "Grbl Streamer Type"),
                ("grbl_streamer", "baudrate", "Baudrate"),
                ("grbl_streamer", "mock_linear_speed_mm_s", "Mock linear speed (mm/s)"),
                ("grbl_streamer", "mock_angular_speed_deg_s", "Mock angular speed (degrees/s)"),
                ("grbl_streamer", "mock_status_hz", "Mock DRO update rate (Hz)"),
                ("windows", "port", "Com Port"),
                ("debug", "serial_comms", "Log serial comms on startup"),
            ):
                if not self.parser.has_section(grbl_sec): continue
                entry = next(e for e in EDITABLE_SCHEMA[grbl_sec] if e[0] == key)
                raw = _strip_inline_comment(self.parser.get(grbl_sec, key, fallback=""))
                options = entry[3]
                if grbl_sec == "windows" and key == "port":
                    options = list(_serial_port_options(raw).keys())
                field, row = self._add_field(
                    layout, grbl_sec, key, label, entry[1], raw, options
                )
                if grbl_sec == "grbl_streamer" and key == "type":
                    grbl_type_input = field
                elif key in {
                    "mock_linear_speed_mm_s",
                    "mock_angular_speed_deg_s",
                    "mock_status_hz",
                }:
                    mock_dro_rows.append(row)

            def update_mock_dro_visibility(streamer_type: str) -> None:
                visible = streamer_type in {"MockSimulatedDRO", "MockWithDRO"}
                for row in mock_dro_rows:
                    row.setVisible(visible)

            if isinstance(grbl_type_input, QComboBox):
                grbl_type_input.currentTextChanged.connect(update_mock_dro_visibility)
                update_mock_dro_visibility(grbl_type_input.currentText())
        elif section == "motion_manager":
            lbl = QLabel("Motion Manager")
            lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
            layout.addWidget(lbl)
            self._build_motion_manager_fields(layout)
        else:
            self._build_fields(layout, section, EDITABLE_SCHEMA.get(section, []))
            
        layout.addStretch(1)
        scroll.setWidget(w)
        index = self.tabs.addTab(scroll, section.upper())
        self.tab_sections[section] = index

    def select_section(self, section: str) -> None:
        index = self.tab_sections.get(section)
        if index is not None:
            self.tabs.setCurrentIndex(index)

    def _build_fields(self, layout, section, schema) -> None:
        visible_keys = CONFIG_EDITOR_SECTION_KEYS.get(section)
        for key, kind, tooltip, options in schema:
            if section == "app" and key == "use_alternative_motion_controls":
                continue
            if visible_keys is not None and key not in visible_keys:
                continue
            if not self.parser.has_option(section, key): continue
            raw = _strip_inline_comment(self.parser.get(section, key))
            label = DISPLAY_LABELS.get(key, key.replace('_', ' ').capitalize())
            self._add_field(layout, section, key, label, kind, raw, options)

    def _build_motion_manager_fields(self, layout) -> None:
        raw_type = _strip_inline_comment(
            self.parser.get("motion_manager", "type", fallback="")
        )
        type_widget, _type_row = self._add_field(
            layout,
            "motion_manager",
            "type",
            "Motion Manager Type",
            "choice",
            raw_type,
            self._options_with_current(list(MOTION_MANAGER_TYPES.keys()), raw_type),
        )

        for key, kind, tooltip, options in self._all_motion_manager_fields():
            raw = _strip_inline_comment(
                self.parser.get("motion_manager", key, fallback="")
            )
            label = DISPLAY_LABELS.get(key, key.replace('_', ' ').title())
            _widget, row = self._add_field(
                layout, "motion_manager", key, label, kind, raw, options
            )
            self.motion_manager_extra_widgets[key] = row

        def update_extra_visibility(manager_type: str) -> None:
            active_keys = {
                key
                for key, _kind, _tooltip, _options in MOTION_MANAGER_TYPES.get(
                    manager_type, []
                )
            }
            for key, row in self.motion_manager_extra_widgets.items():
                row.setVisible(key in active_keys)

        if isinstance(type_widget, QComboBox):
            type_widget.currentTextChanged.connect(update_extra_visibility)
            update_extra_visibility(type_widget.currentText())

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine if hasattr(QFrame, "Shape") else 4)
        separator.setStyleSheet("color: #e2e8f0; margin-top: 8px; margin-bottom: 8px;")
        layout.addWidget(separator)

        lbl = QLabel("Measurement Points")
        lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(lbl)
        self._build_measurement_points_fields(layout)

    def _build_measurement_points_fields(self, layout) -> None:
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

        self.measurement_points_section_input, _section_row = self._add_field(
            layout,
            "__motion_manager_ui__",
            "measurement_points_section",
            "Measurement Points Section",
            "str",
            mp_section_name,
            None,
        )
        self.measurement_points_type_input, _type_row = self._add_field(
            layout,
            "__motion_manager_ui__",
            "measurement_points_type",
            "Measurement Points Type",
            "choice",
            current_type,
            self._options_with_current(list(MEASUREMENT_POINTS_TYPES.keys()), current_type),
        )

        for key, kind, tooltip, options in self._all_measurement_points_fields():
            raw = self._measurement_points_raw_value(mp_section_name, key)
            label = DISPLAY_LABELS.get(key, key.replace('_', ' ').title())
            _widget, row = self._add_field(
                layout, "__measurement_points__", key, label, kind, raw, options
            )
            self.measurement_points_extra_widgets[key] = row

        def update_measurement_points_fields(*_args) -> None:
            mp_type = self._combo_or_text(self.measurement_points_type_input)
            section_name = self._line_text(self.measurement_points_section_input)
            active_keys = {
                key
                for key, _kind, _tooltip, _options in MEASUREMENT_POINTS_TYPES.get(
                    mp_type, []
                )
            }
            for key, row in self.measurement_points_extra_widgets.items():
                row.setVisible(key in active_keys)
                if key in active_keys:
                    widget, _kind = self.inputs[("__measurement_points__", key)]
                    self._set_widget_text(
                        widget, self._measurement_points_raw_value(section_name, key)
                    )

        if isinstance(self.measurement_points_type_input, QComboBox):
            self.measurement_points_type_input.currentTextChanged.connect(
                update_measurement_points_fields
            )
        if isinstance(self.measurement_points_section_input, QLineEdit):
            self.measurement_points_section_input.textChanged.connect(
                update_measurement_points_fields
            )
        update_measurement_points_fields()

    def _all_motion_manager_fields(self):
        seen = set()
        for entries in MOTION_MANAGER_TYPES.values():
            for entry in entries:
                key = entry[0]
                if key in seen:
                    continue
                seen.add(key)
                yield entry

    def _all_measurement_points_fields(self):
        seen = set()
        for entries in MEASUREMENT_POINTS_TYPES.values():
            for entry in entries:
                key = entry[0]
                if key in seen:
                    continue
                seen.add(key)
                yield entry

    def _options_with_current(self, options, current):
        if current and current not in options:
            return [*options, current]
        return options

    def _measurement_points_raw_value(self, section_name: str, key: str) -> str:
        target_section = section_name or "motion_manager"
        if not self.parser.has_section(target_section):
            return ""
        return _strip_inline_comment(self.parser.get(target_section, key, fallback=""))

    def _combo_or_text(self, widget) -> str:
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            return str(data) if data is not None else widget.currentText()
        return self._line_text(widget)

    def _line_text(self, widget) -> str:
        return widget.text().strip() if isinstance(widget, QLineEdit) else ""

    def _set_widget_text(self, widget, raw: str) -> None:
        if isinstance(widget, QComboBox):
            if widget.currentText() != raw:
                widget.setCurrentText(raw)
        elif isinstance(widget, QLineEdit) and widget.text() != raw:
            widget.setText(raw)

    def _add_field(self, layout, section, key, label, kind, raw, options):
        w = QWidget()
        w.setStyleSheet("border: 1px solid #cbd5e1; border-radius: 4px; padding: 4px;")
        if kind == "bool":
            l = QHBoxLayout(w)
            l.setContentsMargins(8, 4, 8, 4)
            l.setSpacing(10)
        else:
            l = QVBoxLayout(w)
            l.setContentsMargins(8, 2, 8, 2)
            l.setSpacing(0)
        
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #64748b; font-size: 10px; border: none;")
        
        if kind == "bool":
            widget = QCheckBox()
            widget.setChecked(_parse_bool(raw))
            widget.setStyleSheet(toggle_style())
            l.addWidget(widget)
            l.addWidget(lbl)
            l.addStretch(1)
        elif kind == "choice" or options:
            l.addWidget(lbl)
            widget = QComboBox()
            light_combo(widget)
            if section == "logging" and key == "level":
                for value in options or []:
                    widget.addItem(LOG_LEVEL_LABELS.get(value, value), value)
                index = widget.findData(raw)
                if index >= 0:
                    widget.setCurrentIndex(index)
            else:
                widget.addItems(options or [])
                widget.setCurrentText(raw)
            widget.setStyleSheet(
                "QComboBox { border: none; background: transparent; "
                "font-size: 13px; color: #0f172a; min-height: 22px; padding: 0; }"
            )
        else:
            l.addWidget(lbl)
            widget = QLineEdit(raw)
            if kind in DECIMAL_TEXT_KINDS:
                widget.textEdited.connect(
                    lambda text, widget=widget: _normalize_decimal_editor_text(widget, text)
                )
            widget.setStyleSheet("border: none; background: transparent; font-size: 13px; color: #0f172a; min-height: 22px; padding: 0;")
            
        if kind != "bool":
            l.addWidget(widget)
        layout.addWidget(w)
        self.inputs[(section, key)] = (widget, kind)
        return widget, w

    def save(self):
        selected_motion_manager_type = None
        motion_manager_widget = self.inputs.get(("motion_manager", "type"))
        if motion_manager_widget is not None:
            widget, _kind = motion_manager_widget
            data = widget.currentData() if isinstance(widget, QComboBox) else None
            selected_motion_manager_type = (
                str(data) if data is not None else widget.currentText()
            )
            if selected_motion_manager_type not in MOTION_MANAGER_TYPES:
                QMessageBox.warning(
                    self,
                    "Error",
                    f"Invalid value for motion manager type: {selected_motion_manager_type}",
                )
                return

        active_motion_manager_keys = set()
        if selected_motion_manager_type is not None:
            active_motion_manager_keys = {
                key
                for key, _kind, _tooltip, _options in MOTION_MANAGER_TYPES[
                    selected_motion_manager_type
                ]
            }

        for (section, key), (widget, kind) in self.inputs.items():
            if section in {"__motion_manager_ui__", "__measurement_points__"}:
                continue
            if section == "motion_manager" and key in self.motion_manager_extra_keys:
                if key not in active_motion_manager_keys:
                    continue

            if kind == "bool":
                raw = "True" if widget.isChecked() else "False"
            elif isinstance(widget, QComboBox):
                data = widget.currentData()
                raw = str(data) if data is not None else widget.currentText()
            else:
                raw = widget.text()
                
            try:
                typed = _coerce(kind, raw)
                if kind == "optional_float" and typed is None:
                    if self.parser.has_option(section, key):
                        self.parser.remove_option(section, key)
                    continue
                self.parser.set(section, key, _format_for_ini(kind, typed))
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Invalid value for {key}: {e}")
                return

        if self.parser.has_section("motion_manager"):
            for key in self.motion_manager_extra_keys - active_motion_manager_keys:
                if self.parser.has_option("motion_manager", key):
                    self.parser.remove_option("motion_manager", key)

        if not self._save_measurement_points_fields():
            return
                
        with open(self.config_file, "w") as f:
            self.parser.write(f)
            
        self.on_apply()
        self.accept()

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
            widget, kind = self.inputs[("__measurement_points__", key)]
            raw = self._combo_or_text(widget) if isinstance(widget, QComboBox) else widget.text()
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
                "safe_radius",
                "measurement_points",
                "measurement_points_type",
            }:
                continue
            if self.parser.has_option(target_section, stale_key):
                self.parser.remove_option(target_section, stale_key)

        return True
