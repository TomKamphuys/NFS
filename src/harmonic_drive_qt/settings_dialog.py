from __future__ import annotations

import configparser
from typing import Callable

from harmonic_drive.config_editor import (
    DISPLAY_LABELS,
    EDITABLE_SCHEMA,
    CONFIG_EDITOR_HIDDEN_SECTIONS,
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


class SettingsDialog(QDialog):
    def __init__(self, config_file: str, on_apply: Callable[[], None], parent=None):
        super().__init__(parent)
        self.config_file = config_file
        self.on_apply = on_apply
        self.parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        self.parser.optionxform = str
        self.parser.read(self.config_file)
        
        self.inputs = {}
        self.tab_sections: dict[str, int] = {}
        
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
        
        for section in EDITABLE_SCHEMA:
            if self.parser.has_section(section) and section not in CONFIG_EDITOR_HIDDEN_SECTIONS:
                self._add_tab(section)
                
        if self.parser.has_section("motion_manager"):
            self._add_tab("motion_manager")
            
        btn_layout = QHBoxLayout()
        restore = QPushButton("RESTORE DEFAULTS")
        restore.setStyleSheet("color: #dc2626; font-weight: bold; padding: 8px 16px; border: 1px solid #fca5a5; border-radius: 4px; background: white;")
        
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
            
            for grbl_sec, key, label in (
                ("grbl_streamer", "type", "Grbl Streamer Type"),
                ("grbl_streamer", "baudrate", "Baudrate"),
                ("grbl_streamer", "mock_linear_speed_mm_s", "Mock Linear Speed Mm/S"),
                ("grbl_streamer", "mock_angular_speed_deg_s", "Mock Angular Speed Deg/S"),
                ("grbl_streamer", "mock_status_hz", "Mock Dro Update Hz"),
                ("windows", "port", "Com Port"),
            ):
                if not self.parser.has_section(grbl_sec): continue
                entry = next(e for e in EDITABLE_SCHEMA[grbl_sec] if e[0] == key)
                raw = _strip_inline_comment(self.parser.get(grbl_sec, key, fallback=""))
                options = entry[3]
                if grbl_sec == "windows" and key == "port":
                    options = list(_serial_port_options(raw).keys())
                self._add_field(layout, grbl_sec, key, label, entry[1], raw, options)
        elif section == "motion_manager":
            lbl = QLabel("Motion Manager configurations are partially supported in native UI.")
            layout.addWidget(lbl)
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
        for key, kind, tooltip, options in schema:
            if section == "app" and key == "use_alternative_motion_controls":
                continue
            if not self.parser.has_option(section, key): continue
            raw = _strip_inline_comment(self.parser.get(section, key))
            label = DISPLAY_LABELS.get(key, key.replace('_', ' ').capitalize())
            self._add_field(layout, section, key, label, kind, raw, options)

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
            widget.setStyleSheet("border: none; background: transparent; font-size: 13px; color: #0f172a; min-height: 22px; padding: 0;")
            
        if kind != "bool":
            l.addWidget(widget)
        layout.addWidget(w)
        self.inputs[(section, key)] = (widget, kind)

    def save(self):
        for (section, key), (widget, kind) in self.inputs.items():
            if kind == "bool":
                raw = "True" if widget.isChecked() else "False"
            elif isinstance(widget, QComboBox):
                data = widget.currentData()
                raw = str(data) if data is not None else widget.currentText()
            else:
                raw = widget.text()
                
            try:
                typed = _coerce(kind, raw)
                self.parser.set(section, key, _format_for_ini(kind, typed))
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Invalid value for {key}: {e}")
                return
                
        with open(self.config_file, "w") as f:
            self.parser.write(f)
            
        self.on_apply()
        self.accept()
