"""Native machine control pane."""

from __future__ import annotations

import configparser
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from harmonic_drive import project
from harmonic_drive.config_editor import _parse_bool, _strip_inline_comment

from .backend import BackendManager, Worker, format_duration
from .icons import ui_icon
from .styles import danger_button, primary_button, warning_button
from .qt_compat import (
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QThreadPool,
    QTimer,
    QVBoxLayout,
    QWidget,
    Qt,
    Signal,
    QSize,
)


def _config_bool(config_file: str, section: str, key: str, fallback: bool) -> bool:
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.read(config_file)
    raw = parser.get(section, key, fallback=str(fallback))
    return _parse_bool(_strip_inline_comment(raw))


class ControlPane(QWidget):
    progress_event = Signal(dict)
    measurement_saved = Signal()

    def __init__(
        self,
        backend: BackendManager,
        require_session_folder: Callable[[], bool] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.backend = backend
        self.require_session_folder = require_session_folder or (lambda: True)
        self.pool = QThreadPool.globalInstance()
        self.sine_running = False
        self.home_ok = False
        self._homing_in_progress = False
        self.grid_readout_points: list[dict[str, float]] = []
        self.progress_event.connect(self._apply_progress_event)
        self._build_ui()

        self.position_timer = QTimer(self)
        self.position_timer.setInterval(250)
        self.position_timer.timeout.connect(self.refresh_position)
        self.position_timer.start()

        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(500)
        self.progress_timer.timeout.connect(self.refresh_progress)
        self.progress_timer.start()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet("background-color: #ffffff;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(0)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 12, 0)
        layout.setSpacing(14)
        scroll.setWidget(content)
        root.addWidget(scroll)

        layout.addWidget(self._build_position_group("WCS"))
        if _config_bool(self.backend.config_file, "app", "show_machine_coordinate_system", False):
            layout.addWidget(self._build_position_group("MCS", machine=True))
        else:
            self.mcs_labels = {}
        layout.addWidget(self._build_motion_group())
        if _config_bool(self.backend.config_file, "app", "show_height_offset_controls", True):
            layout.addWidget(self._build_height_offset_group())
        layout.addWidget(self._build_measurement_group())
        layout.addStretch(1)

    def _build_position_group(self, title: str, machine: bool = False) -> QFrame:
        frame = QFrame()
        frame.setObjectName("DroFrame")
        frame.setStyleSheet("QFrame#DroFrame { background-color: #000000; border-radius: 6px; border: 2px solid #1f2937; }")
        frame.setMaximumWidth(780)
        frame.setMinimumHeight(72)
        frame.setMaximumHeight(86)
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 7, 0, 8)
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)
        
        # Add a small vertical title block on the left
        title_label = QLabel(title[0] + "\n" + title[1] + "\n" + title[2])
        title_label.setStyleSheet(
            f"color: {'#ff8c00' if machine else '#7eff00'}; font-weight: 900; font-size: 10px; "
            "background: #000000; border: none; border-right: 2px solid #1f2937;"
        )
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setFixedWidth(40)
        grid.addWidget(title_label, 0, 0, 2, 1)

        labels = {}
        color = "#ff8c00" if machine else "#7eff00"
        
        columns = [("PHI DEG", "T"), ("RADIUS MM", "R"), ("HEIGHT MM", "Z"), ("STATUS MODE", "STATE")]
        
        for col, (display_name, axis) in enumerate(columns):
            header = QLabel(display_name)
            border = "border-right: 2px solid #1f2937;" if col < len(columns) - 1 else ""
            header.setStyleSheet(
                f"background: #000000; color: #f8fafc; font-weight: 800; font-size: 9px; "
                f"border: none; {border}"
            )
            header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(header, 0, col + 1)
            
            label = QLabel("--")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(
                f"background: #000000; color: {color}; font-size: 31px; "
                f"font-family: Consolas, monospace; font-weight: bold; border: none; {border} padding: 0px;"
            )
            grid.addWidget(label, 1, col + 1)
            labels[axis] = label
            grid.setColumnStretch(col + 1, 1)
            
        if machine:
            self.mcs_labels = labels
        else:
            self.wcs_labels = labels
        return frame

    def _build_motion_group(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("background-color: #000000; border-radius: 6px; border: 2px solid #1f2937;")
        frame.setMaximumWidth(780)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        # M O V E label
        main_h = QHBoxLayout()
        main_h.setSpacing(8)
        move_label = QLabel("M\nO\nV\nE")
        move_label.setStyleSheet("color: #2f67b7; font-weight: 900; font-size: 11px; border: none;")
        move_label.setFixedWidth(28)
        move_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_h.addWidget(move_label)

        jog_layout = QVBoxLayout()
        jog_layout.setSpacing(4)
        for axis, left, right, unit, left_method, right_method in [
            ("PHI", "CW", "CCW", "deg", "rotate_cw", "rotate_ccw"),
            ("RADIUS", "-", "+", "mm", "move_in", "move_out"),
            ("HEIGHT", "-", "+", "mm", "move_down", "move_up"),
        ]:
            jog_layout.addLayout(self._jog_row(axis, left, right, unit, left_method, right_method))
        
        main_h.addLayout(jog_layout)
        layout.addLayout(main_h)

        commands = QHBoxLayout()
        commands.setSpacing(8)
        show_rehome = _config_bool(self.backend.config_file, "app", "show_rehome_button", False)
        command_width = 118 if show_rehome else 132
        
        home = QPushButton("HOME")
        self.home_button = home
        self._set_home_button_state(False)
        home.setIcon(ui_icon("home"))
        home.setIconSize(QSize(20, 20))
        home.setFixedSize(command_width, 46)
        home.clicked.connect(lambda: self._run_command("home"))
        commands.addWidget(home)

        if show_rehome:
            rehome = QPushButton("REHOME")
            warning_button(rehome)
            rehome.setIcon(ui_icon("home"))
            rehome.setIconSize(QSize(20, 20))
            rehome.setFixedSize(command_width, 46)
            rehome.clicked.connect(lambda: self._run_command("rehome"))
            commands.addWidget(rehome)

        zero = QPushButton("ZERO")
        primary_button(zero)
        zero.setIcon(ui_icon("target"))
        zero.setIconSize(QSize(20, 20))
        zero.setFixedSize(command_width, 46)
        zero.clicked.connect(lambda: self._run_command("set_as_zero"))
        commands.addWidget(zero)

        clear = QPushButton("CLEAR\nALARM")
        primary_button(clear)
        clear.setIcon(ui_icon("alarm-clear"))
        clear.setIconSize(QSize(20, 20))
        clear.setFixedSize(command_width, 46)
        clear.clicked.connect(lambda: self._run_command("clear_alarm"))
        commands.addWidget(clear)

        reset = QPushButton("RESET")
        danger_button(reset)
        reset.setIcon(ui_icon("reset"))
        reset.setIconSize(QSize(20, 20))
        reset.setFixedSize(command_width, 46)
        reset.clicked.connect(lambda: self._run_command("softreset"))
        commands.addWidget(reset)

        hold = QPushButton("HOLD")
        danger_button(hold)
        hold.setIcon(ui_icon("stop"))
        hold.setIconSize(QSize(20, 20))
        hold.setFixedSize(command_width, 46)
        hold.clicked.connect(lambda: self._run_command("hold"))
        commands.addWidget(hold)

        layout.addLayout(commands)

        return frame

    def _jog_row(self, axis: str, left: str, right: str, unit: str, left_method: str, right_method: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(5)
        
        def button_style(value: int) -> str:
            palette = {
                120: ("#255296", "#36609e", "#ffffff"),
                60: ("#3d74bb", "#4c7fc0", "#ffffff"),
                20: ("#4d86cd", "#5b8fd1", "#ffffff"),
                10: ("#5f9add", "#6ca2e0", "#ffffff"),
                5: ("#82b9f0", "#8cbff1", "#08111f"),
                1: ("#a6d1fd", "#add5fd", "#08111f"),
            }
            background, hover, text = palette.get(value, ("#3e6f9e", "#34618b", "#ffffff"))
            return (
                "QPushButton { "
                f"background: {background}; color: {text}; "
                "font-weight: 900; border-radius: 3px; padding: 7px 4px; border: 1px solid #020617; }"
                f"QPushButton:hover {{ background: {hover}; color: #ffffff; }}"
            )

        left_btn = QPushButton(left)
        left_btn.setStyleSheet("QPushButton { background: transparent; color: white; font-weight: bold; border: none; min-width: 32px; }")
        left_btn.setFixedSize(32, 34)
        row.addWidget(left_btn)
        left_btn.clicked.connect(lambda _checked=False, m=left_method, v=120: self._run_jog(m, v)) # Just an example default

        for value in (120, 60, 20, 10, 5, 1):
            button = QPushButton(str(value))
            button.setStyleSheet(button_style(value))
            button.setFixedSize(48, 34)
            button.clicked.connect(lambda _checked=False, m=left_method, v=value: self._run_jog(m, v))
            row.addWidget(button)
            
        axis_label = QLabel(f"{axis}\n{unit.upper()}")
        axis_label.setStyleSheet("color: white; font-weight: 900; font-size: 10px; border: none;")
        axis_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        axis_label.setFixedWidth(50)
        row.addWidget(axis_label)
        
        for value in (1, 5, 10, 20, 60, 120):
            button = QPushButton(str(value))
            button.setStyleSheet(button_style(value))
            button.setFixedSize(48, 34)
            button.clicked.connect(lambda _checked=False, m=right_method, v=value: self._run_jog(m, v))
            row.addWidget(button)

        right_btn = QPushButton(right)
        right_btn.setStyleSheet("QPushButton { background: transparent; color: white; font-weight: bold; border: none; min-width: 32px; }")
        right_btn.setFixedSize(32, 34)
        row.addWidget(right_btn)
        right_btn.clicked.connect(lambda _checked=False, m=right_method, v=120: self._run_jog(m, v))
        
        return row

    def _build_height_offset_group(self) -> QGroupBox:
        group = QGroupBox("Height Offset")
        row = QHBoxLayout(group)
        self.height_offset = QDoubleSpinBox()
        self.height_offset.setRange(-2000, 2000)
        self.height_offset.setDecimals(2)
        self.height_offset.setSuffix(" mm")
        self.height_offset.setValue(0.0)
        apply = QPushButton("Set Height Offset")
        apply.clicked.connect(
            lambda: self._run_backend(
                self.backend.set_speaker_center_above_stool,
                self.height_offset.value(),
            )
        )
        row.addWidget(QLabel("Speaker center above stool"))
        row.addWidget(self.height_offset)
        row.addWidget(apply)
        return group

    def _build_audio_group(self) -> QGroupBox:
        group = QGroupBox("Audio")
        grid = QGridLayout(group)
        self.sine_freq = QDoubleSpinBox()
        self.sine_freq.setRange(10, 40000)
        self.sine_freq.setValue(1000)
        self.sine_freq.setSuffix(" Hz")
        self.sine_level = QDoubleSpinBox()
        self.sine_level.setRange(-120, 0)
        self.sine_level.setValue(-20)
        self.sine_level.setSuffix(" dBFS")
        self.sine_duration = QDoubleSpinBox()
        self.sine_duration.setRange(0.1, 3600)
        self.sine_duration.setValue(5)
        self.sine_duration.setSuffix(" s")
        self.sine_button = QPushButton("Play Sine")
        self.sine_button.clicked.connect(self.toggle_sine)
        test_button = QPushButton("Test Sweep")
        test_button.clicked.connect(self.test_sweep)

        grid.addWidget(QLabel("Frequency"), 0, 0)
        grid.addWidget(self.sine_freq, 0, 1)
        grid.addWidget(QLabel("Level"), 1, 0)
        grid.addWidget(self.sine_level, 1, 1)
        grid.addWidget(QLabel("Duration"), 2, 0)
        grid.addWidget(self.sine_duration, 2, 1)
        grid.addWidget(self.sine_button, 3, 0)
        grid.addWidget(test_button, 3, 1)
        return group

    def _build_measurement_group(self) -> QGroupBox:
        group = QGroupBox("Measurements")
        layout = QVBoxLayout(group)
        layout.setSpacing(12)

        single_row = QHBoxLayout()
        single = QPushButton("TAKE SINGLE MEASUREMENT")
        primary_button(single)
        single.setMinimumHeight(36)
        single.setFixedWidth(260)
        single.clicked.connect(self.take_single_measurement)
        single_row.addWidget(single)
        single_row.addStretch(1)
        layout.addLayout(single_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(16)
        self.measurement_start = QPushButton("START MEASUREMENTS")
        primary_button(self.measurement_start)
        self.measurement_start.setIcon(ui_icon("play"))
        self.measurement_start.setIconSize(QSize(18, 18))
        self.measurement_start.setMinimumHeight(36)
        self.measurement_start.setFixedWidth(260)
        self.measurement_start.clicked.connect(self.toggle_measurement_set)
        
        self.measurement_stop = QPushButton("STOP MEASUREMENTS")
        danger_button(self.measurement_stop)
        self.measurement_stop.setIcon(ui_icon("stop"))
        self.measurement_stop.setIconSize(QSize(18, 18))
        self.measurement_stop.setMinimumHeight(36)
        self.measurement_stop.setFixedWidth(260)
        self.measurement_stop.clicked.connect(self.backend.stop_measurement_set)
        buttons.addWidget(self.measurement_start)
        buttons.addWidget(self.measurement_stop)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFixedWidth(536)
        self.progress.setFixedHeight(28)
        self.progress.setTextVisible(True)
        self.progress.setFormat("Ready - waiting for measurement points")
        self.progress.setStyleSheet(
            "QProgressBar { border: 1px solid #bfdbfe; border-radius: 4px; background: #eff6ff; "
            "color: #0f172a; font-weight: 800; text-align: center; }"
            "QProgressBar::chunk { background: #3978bd; border-radius: 3px; }"
        )
        layout.addWidget(self.progress)
        self.coord_readout = QLabel("No grid coordinates loaded")
        self.coord_readout.setFixedWidth(536)
        self.coord_readout.setTextFormat(Qt.TextFormat.RichText)
        self.coord_readout.setStyleSheet(
            "font-family: Consolas, monospace; color: #334155; border: 1px solid #cbd5e1; "
            "border-radius: 4px; background: #f8fafc; padding: 7px 9px; font-size: 13px;"
        )
        layout.addWidget(self.coord_readout)
        return group

    def _set_home_button_state(self, homed: bool) -> None:
        color = "#22c55e" if homed else "#f4c542"
        hover = "#16a34a" if homed else "#e5b834"
        self.home_button.setStyleSheet(
            f"QPushButton {{ background: {color}; border: 1px solid {color}; "
            "border-radius: 4px; color: #ffffff; font-weight: 800; padding: 7px 11px; }"
            f"QPushButton:hover {{ background: {hover}; border-color: {hover}; }}"
        )

    def _ensure_session_folder(self) -> bool:
        return bool(self.require_session_folder())

    def take_single_measurement(self) -> None:
        if not self._ensure_session_folder():
            return
        self._save_measurement_project_snapshot()
        self._run_measurement_backend(self.backend.take_single_measurement)

    def test_sweep(self) -> None:
        worker = Worker(self.backend.test_sweep)
        worker.signals.failed.connect(lambda message: QMessageBox.warning(self, "Test Sweep", message))
        worker.signals.finished.connect(self.measurement_saved.emit)
        self.pool.start(worker)

    def _run_backend(self, func, *args) -> None:
        worker = Worker(func, *args)
        worker.signals.failed.connect(lambda message: QMessageBox.warning(self, "Backend Error", message))
        self.pool.start(worker)

    def _run_measurement_backend(self, func, *args) -> None:
        worker = Worker(func, *args)
        worker.signals.failed.connect(lambda message: QMessageBox.warning(self, "Backend Error", message))
        worker.signals.finished.connect(self.measurement_saved.emit)
        self.pool.start(worker)

    def _run_command(self, method: str) -> None:
        if method == "set_as_zero":
            self._run_backend(self.backend.set_as_zero)
        elif method == "home":
            self._homing_in_progress = True
            self._set_home_ok(False)
            worker = Worker(self._home_blocking)
            worker.signals.finished.connect(self._home_finished)
            worker.signals.failed.connect(self._home_failed)
            self.pool.start(worker)
        elif method == "rehome":
            self._homing_in_progress = True
            self._set_home_ok(False)
            worker = Worker(self._rehome_blocking)
            worker.signals.finished.connect(self._home_finished)
            worker.signals.failed.connect(self._home_failed)
            self.pool.start(worker)
        else:
            if method == "softreset":
                self._set_home_ok(False)
            self._run_backend(self.backend.scanner_command, method)

    def _set_home_ok(self, ok: bool) -> None:
        self.home_ok = bool(ok)
        self._set_home_button_state(self.home_ok)

    def _home_finished(self) -> None:
        self._homing_in_progress = False
        self._set_home_ok(True)

    def _home_failed(self, message: str) -> None:
        self._homing_in_progress = False
        self._set_home_ok(False)
        QMessageBox.warning(self, "Backend Error", message)

    def _rehome_blocking(self) -> None:
        self.backend.scanner_command("softreset")
        time.sleep(1)
        self.backend.scanner_command("clear_alarm")
        time.sleep(1)
        self.backend.scanner_command("home")

    def _home_blocking(self) -> None:
        self.backend.scanner_command("home")

    def _run_jog(self, method: str, value: float) -> None:
        self._run_backend(self.backend.jog, method, value)

    def toggle_sine(self) -> None:
        if self.sine_running:
            self.backend.stop_sine()
            self.sine_running = False
            self.sine_button.setText("Play Sine")
            return
        self.sine_running = True
        self.sine_button.setText("Stop Sine")
        self._run_backend(
            self.backend.play_sine,
            self.sine_freq.value(),
            self.sine_level.value(),
            self.sine_duration.value(),
        )

    def toggle_measurement_set(self) -> None:
        if self.backend.is_measurement_set_paused():
            self.backend.resume_measurement_set()
        elif self.backend.is_measurement_set_running():
            self.backend.pause_measurement_set()
        else:
            if not self._ensure_session_folder():
                return
            if not self._loaded_grid_file_exists(project.get_project_dir()):
                QMessageBox.warning(self, "Measurement Set", "No grid file loaded. Please generate one first.")
                return
            overwrite = False
            if self._measurement_outputs_exist(project.get_project_dir()):
                result = QMessageBox.question(
                    self,
                    "Measurement Output Already Exists",
                    "This measurement folder already contains measurement output.\n\n"
                    "Overwrite the previous output and start again?",
                    QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                    QMessageBox.StandardButton.Cancel,
                )
                if result != QMessageBox.StandardButton.Yes:
                    return
                overwrite = True
                self._clear_measurement_outputs(project.get_project_dir())
            title = project.get_project_name() or project.DEFAULT_PROJECT_NAME
            self._save_measurement_project_snapshot()
            self.refresh_grid_readout()
            self.backend.start_measurement_set(
                project.sanitize_project_name(title),
                lambda event: self.progress_event.emit(event),
                overwrite=overwrite,
            )
        self.refresh_progress()

    def _save_measurement_project_snapshot(self) -> None:
        title = project.get_project_name() or project.DEFAULT_PROJECT_NAME
        project.set_project_name(title)
        saved_dir = project.save_project_to(project.get_project_dir(), title, self.backend.config_file)
        self.backend.set_project_dir(saved_dir)

    def _load_grid_readout_points(self) -> None:
        if self.grid_readout_points:
            return
        self._reload_grid_readout_points()

    def refresh_grid_readout(self, current_index: int = 0) -> None:
        self.grid_readout_points = []
        self._reload_grid_readout_points()
        self._update_grid_readout(current_index)

    def _reload_grid_readout_points(self) -> None:
        path = project.get_project_dir() / str(project.get_grid_filename())
        if not path.exists():
            grid_vars = project.get_project_data().get("grid_vars")
            if isinstance(grid_vars, dict) and grid_vars.get("output_filename"):
                path = project.get_project_dir() / str(grid_vars["output_filename"])
        if not path.exists():
            self.grid_readout_points = []
            return
        try:
            import pandas as pd

            df = pd.read_csv(path)
            required = {"r_xy_mm", "phi_deg", "z_mm"}
            if not required.issubset(df.columns):
                self.grid_readout_points = []
                return
            points = []
            for r, phi, z in zip(df["r_xy_mm"], df["phi_deg"], df["z_mm"]):
                radius = float(r)
                angle = float(phi)
                points.append(
                    {
                        "r": radius,
                        "p": angle,
                        "z": float(z),
                    }
                )
            self.grid_readout_points = points
        except Exception:
            self.grid_readout_points = []

    def _loaded_grid_file_exists(self, root: Path) -> bool:
        grid_vars = project.get_project_data().get("grid_vars")
        if isinstance(grid_vars, dict):
            filename = grid_vars.get("output_filename")
            if filename and (root / str(filename)).exists():
                return True
        return False

    def _measurement_outputs_exist(self, root: Path) -> bool:
        measurement_dir = root / "measurement_set"
        if not measurement_dir.exists():
            return False
        return any(path.is_file() for path in measurement_dir.rglob("*"))

    def _clear_measurement_outputs(self, root: Path) -> None:
        measurement_dir = root / "measurement_set"
        if measurement_dir.exists():
            shutil.rmtree(measurement_dir)
        for filename in ("measurement_positions.csv",):
            path = root / filename
            if path.exists():
                path.unlink()
        log_file = root / "logs" / "Scanner.log"
        if log_file.exists():
            log_file.unlink()

    def refresh_position(self) -> None:
        pos = self.backend.get_position()
        state = self.backend.get_state()
        machine_pos = self.backend.get_machine_position() if self.mcs_labels else None
        state_name = str(state).split(".")[-1] if state is not None else ""
        if state_name.upper() == "ALARM" and not self._homing_in_progress:
            self._set_home_ok(False)
        self._set_position_labels(self.wcs_labels, pos, state)
        if self.mcs_labels:
            self._set_position_labels(self.mcs_labels, machine_pos, state)

    def _set_position_labels(self, labels: dict[str, QLabel], pos, state) -> None:
        def value(name: str) -> str:
            if pos is None:
                return "--"
            for attr in (name.lower(), f"pos_{name.lower()}"):
                if hasattr(pos, attr):
                    try:
                        raw = getattr(pos, attr)
                        if callable(raw):
                            raw = raw()
                        return f"{float(raw):.2f}"
                    except Exception:
                        return str(raw)
            return "--"

        labels["T"].setText(value("t"))
        labels["R"].setText(value("r"))
        labels["Z"].setText(value("z"))
        labels["STATE"].setText(str(state).split(".")[-1] if state is not None else "--")

    def refresh_progress(self) -> None:
        self._apply_progress_event(self.backend.get_measurement_progress())
        if self.backend.is_measurement_set_paused():
            self.measurement_start.setText("RESUME")
            primary_button(self.measurement_start)
            self.measurement_start.setIcon(ui_icon("play"))
        elif self.backend.is_measurement_set_running():
            self.measurement_start.setText("PAUSE")
            warning_button(self.measurement_start)
            self.measurement_start.setIcon(ui_icon("pause"))
        else:
            self.measurement_start.setText("START MEASUREMENT SET")
            primary_button(self.measurement_start)
            self.measurement_start.setIcon(ui_icon("play"))
        self.measurement_start.setIconSize(QSize(18, 18))

    def _apply_progress_event(self, event: dict) -> None:
        current = int(event.get("current") or 0)
        total = int(event.get("total") or 0)
        status = str(event.get("status") or "ready")
        if status == "finished" and total > 0 and current >= total:
            label_status = "Complete"
        elif status == "finished":
            label_status = "Stopped"
        elif status == "ready":
            label_status = "Ready"
        else:
            label_status = "Running"
        percent = current / total if total else 0.0
        self.progress.setValue(int(max(0.0, min(1.0, percent)) * 1000))
        self._update_grid_readout(max(0, current - 1 if current else 0))
        eta = format_duration(event.get("eta_seconds"))
        if total:
            self.progress.setFormat(f"{label_status} - {current} of {total} points - ETA {eta}")
        else:
            self.progress.setFormat(f"{label_status} - waiting for measurement points")

    def _update_grid_readout(self, current_index: int) -> None:
        if not hasattr(self, "coord_readout"):
            return
        self._load_grid_readout_points()
        if not self.grid_readout_points:
            self.coord_readout.setText("No grid coordinates loaded")
            return
        current_index = max(0, min(current_index, len(self.grid_readout_points) - 1))
        rows = [
            "<tr style='color:#64748b; font-size:12px; font-weight:800;'>"
            "<td style='width:18px; padding:0 4px 4px 0; text-align:center;'></td>"
            "<td style='padding:0 8px 4px 0;'>Point</td>"
            "<td style='padding:0 12px 4px 0; text-align:right;'>Radius</td>"
            "<td style='padding:0 12px 4px 0; text-align:right;'>Phi</td>"
            "<td style='padding:0 2px 4px 0; text-align:right;'>Height</td>"
            "</tr>"
        ]
        for offset in (-1, 0, 1, 2, 3):
            idx = current_index + offset
            if 0 <= idx < len(self.grid_readout_points):
                point = self.grid_readout_points[idx]
                marker = "&#9654;" if offset == 0 else "&nbsp;"
                label = f"{idx}"
                style = (
                    "background:#dbeafe; color:#0f172a; font-weight:900;"
                    if offset == 0
                    else "color:#334155;"
                )
                rows.append(
                    f"<tr style='{style}'>"
                    f"<td style='width:18px; padding:3px 4px 3px 0; text-align:center; color:#2563eb;'>{marker}</td>"
                    f"<td style='padding:3px 8px 3px 0;'>{label}</td>"
                    f"<td style='padding:3px 12px 3px 0; text-align:right;'>{point['r']:7.1f} mm</td>"
                    f"<td style='padding:3px 12px 3px 0; text-align:right;'>{point['p']:7.1f}&deg;</td>"
                    f"<td style='padding:3px 2px 3px 0; text-align:right;'>{point['z']:7.1f} mm</td>"
                    "</tr>"
                )
            else:
                rows.append(
                    "<tr style='color:#94a3b8;'>"
                    "<td style='width:18px; padding:3px 4px 3px 0;'>&nbsp;</td>"
                    "<td style='padding:3px 8px 3px 0;'>--</td>"
                    "<td colspan='3' style='padding:3px 2px 3px 0;'>No point</td>"
                    "</tr>"
                )
        self.coord_readout.setText(
            "<table style='margin:0; border-collapse:collapse; font-family:Consolas, monospace; "
            "font-size:13px; width:100%;'>"
            + "".join(rows)
            + "</table>"
        )
