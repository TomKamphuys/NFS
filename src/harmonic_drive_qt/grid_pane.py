"""Native grid generator and replay pane."""

from __future__ import annotations

import sys
import configparser
from collections.abc import Callable
from pathlib import Path

import pandas as pd
from loguru import logger
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar

from harmonic_drive import project
from grid_generator.grid_gen import generate_measurement_grid
from grid_generator.path_plan import plan_path

from .backend import BackendManager, Worker
from .styles import light_combo, primary_button, toggle_style
from .qt_compat import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QAbstractSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPixmap,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QThreadPool,
    QTimer,
    QVBoxLayout,
    QWidget,
    Qt,
    Signal,
)

GRID_SRC = Path(__file__).resolve().parents[1] / "grid"
if str(GRID_SRC) not in sys.path:
    sys.path.insert(0, str(GRID_SRC))

GRID_IMAGE_DIR = Path(__file__).resolve().parents[1] / "grid_generator" / "images_grid_gen"

from coord_viewer_core import CoordViewerEngine  # noqa: E402


class DiagramLabel(QLabel):
    def __init__(self, text: str, image_path: Path | None = None, parent=None) -> None:
        super().__init__(text, parent)
        self.image_path = image_path
        self.popup: QFrame | None = None

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.image_path is None or not self.image_path.exists():
            return super().enterEvent(event)
        popup = QFrame(None, Qt.WindowType.ToolTip)
        popup.setStyleSheet("background: white; border: 1px solid #cbd5e1; padding: 4px;")
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(4, 4, 4, 4)
        image = QLabel()
        pixmap = QPixmap(str(self.image_path))
        if not pixmap.isNull():
            image.setPixmap(pixmap.scaledToWidth(256, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(image)
        popup.adjustSize()
        popup.move(self.mapToGlobal(self.rect().bottomLeft()))
        popup.show()
        self.popup = popup
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.popup is not None:
            self.popup.close()
            self.popup = None
        super().leaveEvent(event)


class GridGeneratorPane(QWidget):
    grid_saved = Signal(str, dict)
    generated = Signal(object, str)

    def __init__(
        self,
        backend: BackendManager,
        config_file: str,
        require_session_folder: Callable[[], bool] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.backend = backend
        self.config_file = config_file
        self.require_session_folder = require_session_folder or (lambda: True)
        self.pool = QThreadPool.globalInstance()
        self.viewer_backend = self._read_viewer_backend()
        self.current_viewer_input = None
        self.canvas = None
        self.viewer_widget: QWidget | None = None
        self.viewer_layout: QVBoxLayout | None = None
        self._pyvista_error: str | None = None
        self._pyvista_fallback_message_shown = False
        self.engine = self._create_viewer_engine(self.viewer_backend)
        self.viewer_more_popup: QFrame | None = None
        self.generated.connect(self._load_dataframe_on_ui)
        self._build_ui()
        if self._pyvista_error is not None:
            QTimer.singleShot(0, self._show_pyvista_fallback_message)
        self._load_existing_project_grid()

        self.sync_timer = QTimer(self)
        self.sync_timer.setInterval(100)
        self.sync_timer.timeout.connect(self._sync_viewer_controls)
        self.sync_timer.start()

    def _read_viewer_backend(self) -> str:
        parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        parser.read(self.config_file)
        backend = parser.get("app", "coord_viewer_backend", fallback="matplotlib").strip().lower()
        return backend if backend in {"matplotlib", "pyvista"} else "matplotlib"

    def _create_viewer_engine(self, backend: str):
        if backend == "pyvista":
            try:
                from coord_viewer_core_pyvista import CoordViewerPyVista  # noqa: PLC0415

                self._pyvista_error = None
                return CoordViewerPyVista()
            except Exception as exc:
                self._pyvista_error = str(exc)
                logger.exception("Could not create PyVista coordinate viewer; falling back to Matplotlib")
                self.viewer_backend = "matplotlib"
        return CoordViewerEngine()

    def _build_ui(self) -> None:
        self.setStyleSheet("background-color: #ffffff;")
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 0, 12, 16)
        root.setSpacing(16)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)
        scroll.setWidget(content_widget)
        root.addWidget(scroll)

        # Main Card for Generation
        gen_card = QFrame()
        gen_card.setObjectName("Card")
        gen_layout = QVBoxLayout(gen_card)
        gen_layout.setContentsMargins(20, 20, 20, 20)
        
        title = QLabel("Grid Generation & Planning")
        title.setStyleSheet("font-size: 20px; font-weight: 800; color: #000000; border: none;")
        gen_layout.addWidget(title)
        
        subtitle = QLabel("Cylinder Physical Waypoints - Jog and Set Position")
        subtitle.setStyleSheet("font-size: 14px; font-weight: bold; color: #1e293b; border: none; margin-top: 8px;")
        gen_layout.addWidget(subtitle)
        
        desc = QLabel("Top, Bottom, and Tweeter waypoints are required. Ref Origin, baffle corners, and additional points help visualise exported response data.")
        desc.setStyleSheet("color: #64748b; font-size: 12px; border: none;")
        desc.setWordWrap(True)
        gen_layout.addWidget(desc)
        gen_layout.addSpacing(12)
        
        form_block = QVBoxLayout()
        form_block.setSpacing(8)
        form_row = QHBoxLayout()
        form_row.setSpacing(4)
        
        # We need to construct the form groups first so we can place them
        self._init_waypoint_inputs()
        self.output_filename = QLineEdit(project.get_grid_filename())
        
        left_col = QVBoxLayout()
        right_col = QVBoxLayout()
        left_col.setSpacing(6)
        right_col.setSpacing(6)
        
        left_col.addWidget(self._build_waypoint_row("Top Waypoint:", "top", "waypoint_top.png"))
        left_col.addWidget(self._build_waypoint_row("Bot Waypoint:", "bottom", "waypoint_bottom.png"))
        left_col.addWidget(self._build_waypoint_row("Tweeter Point:", "tweeter", "tweeter_point.png"))
        
        right_col.addWidget(self._build_waypoint_row("Ref Origin:", "ref_origin"))
        right_col.addWidget(self._build_waypoint_row("Baffle Bot L:", "baffle_bot_l"))
        right_col.addWidget(self._build_waypoint_row("Baffle Top L:", "baffle_top_l"))
        right_col.addWidget(self._build_waypoint_row("Baffle Top R:", "baffle_top_r"))
        advanced_settings = self._build_settings_group()
        
        # Stats below right col
        stats_row = QHBoxLayout()
        stats_row.addWidget(self._labeled_stat("Total Points", self.num_points))
        stats_row.addWidget(self._labeled_stat("Azimuth Density Ratio", self.az_density))
        right_col.addLayout(stats_row)
        right_col.addStretch(1)
        
        self.extra_positions_layout = QVBoxLayout()
        self.extra_positions_layout.setSpacing(6)
        left_col.addLayout(self.extra_positions_layout)
        for position in self.pending_extra_positions:
            self._add_extra_position(position)

        extra_widget = QWidget()
        extra_widget.setStyleSheet("border: none; background: transparent;")
        extra_widget.setFixedHeight(42)
        extra_row = QHBoxLayout(extra_widget)
        extra_row.setContentsMargins(16, 0, 0, 0)
        extra_row.setSpacing(8)
        add_btn = QPushButton("+")
        add_btn.setFixedSize(32, 30)
        add_btn.setStyleSheet("color: #3b82f6; font-size: 24px; font-weight: 900; border: none; background: transparent; padding: 0; min-width: 0;")
        add_btn.clicked.connect(self._add_extra_position)
        rem_btn = QPushButton("-")
        rem_btn.setFixedSize(32, 30)
        rem_btn.setStyleSheet("color: #ef4444; font-size: 24px; font-weight: 900; border: none; background: transparent; padding: 0; min-width: 0;")
        rem_btn.clicked.connect(self._remove_extra_position)
        extra_row.addWidget(add_btn)
        extra_row.addWidget(rem_btn)
        extra_lbl = QLabel("Additional Points")
        extra_lbl.setStyleSheet("font-weight: bold; border: none;")
        extra_row.addWidget(extra_lbl)
        extra_row.addStretch(1)
        left_col.addWidget(extra_widget)
        left_col.addStretch(1)

        form_row.addLayout(left_col, 1)
        form_row.addLayout(right_col, 1)
        form_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        form_block.addLayout(form_row)
        form_block.addWidget(advanced_settings)
        gen_layout.addLayout(form_block)
        
        gen_layout.addSpacing(16)
        
        generate = QPushButton("GENERATE & PLAN PATH")
        primary_button(generate)
        generate.setMinimumHeight(38)
        generate.clicked.connect(self.generate_and_plan)
        gen_layout.addWidget(generate)
        
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #64748b; border: none;")
        gen_layout.addWidget(self.status_label)
        
        content_layout.addWidget(gen_card)
        
        # Playback Card
        play_card = QFrame()
        play_card.setObjectName("Card")
        play_layout = QVBoxLayout(play_card)
        play_layout.setContentsMargins(12, 16, 12, 16)
        
        scrub_row = QHBoxLayout()
        scrub_lbl = QLabel("Scrub:")
        scrub_lbl.setStyleSheet("font-weight: bold; border: none;")
        scrub_row.addWidget(scrub_lbl)
        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.setRange(0, 0)
        self.scrub.setStyleSheet("border: none;")
        self.scrub.valueChanged.connect(self._set_current_index)
        scrub_row.addWidget(self.scrub)
        play_layout.addLayout(scrub_row)
        
        play_layout.addLayout(self._build_viewer_controls())
        content_layout.addWidget(play_card)
        
        # Viewer Card
        view_card = QFrame()
        view_card.setObjectName("Card")
        view_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.viewer_layout = QVBoxLayout(view_card)
        self.viewer_layout.setContentsMargins(4, 4, 4, 4)
        self._install_viewer_widget()
        content_layout.addWidget(view_card, 1)

    def _install_viewer_widget(self) -> None:
        if self.viewer_layout is None:
            return

        if self.viewer_widget is not None:
            self.viewer_layout.removeWidget(self.viewer_widget)
            self.viewer_widget.setParent(None)
            self.viewer_widget.deleteLater()
            self.viewer_widget = None

        if self.viewer_backend == "pyvista":
            widget = self.engine
            widget.setMinimumHeight(700)
            self.canvas = None
        else:
            widget = FigureCanvas(self.engine.fig)
            widget.setMinimumHeight(360)
            self.canvas = widget

        if self.viewer_backend != "pyvista":
            widget.setMinimumHeight(360)
        self.viewer_layout.addWidget(widget)
        self.viewer_widget = widget

    def _init_waypoint_inputs(self) -> None:
        self.waypoint_inputs: dict[str, tuple[QDoubleSpinBox, QDoubleSpinBox, QDoubleSpinBox]] = {}
        self.extra_position_rows: list[dict[str, object]] = []
        self.pending_extra_positions: list[dict] = []
        rows = [
            ("Top", "top", "wp_top", 200.0, 45.0, 350.0),
            ("Bottom", "bottom", "wp_bot", 40.0, 0.0, -50.0),
            ("Tweeter", "tweeter", "wp_tw", 150.0, 0.0, 250.0),
            ("Ref Origin", "ref_origin", "wp_ref_origin", 90.0, 0.0, 170.0),
            ("Baffle Bot L", "baffle_bot_l", "wp_baffle_bl", 90.0, -45.0, 80.0),
            ("Baffle Top L", "baffle_top_l", "wp_baffle_tl", 90.0, -45.0, 240.0),
            ("Baffle Top R", "baffle_top_r", "wp_baffle_tr", 90.0, 45.0, 240.0),
        ]
        grid_vars = project.get_project_data().get("grid_vars", {})
        grid_vars = grid_vars if isinstance(grid_vars, dict) else {}
        for (label, key, saved_key, r_default, phi_default, z_default) in rows:
            r = self._spin(self._gv_float(grid_vars, f"{saved_key}_r", r_default), -2000, 2000, "")
            phi = self._spin(self._gv_float(grid_vars, f"{saved_key}_phi", phi_default), -360, 360, "")
            z = self._spin(self._gv_float(grid_vars, f"{saved_key}_z", z_default), -2000, 2000, "")
            self.waypoint_inputs[key] = (r, phi, z)
            
        saved_positions = grid_vars.get("user_positions")
        if isinstance(saved_positions, list):
            for position in saved_positions:
                if isinstance(position, dict):
                    self.pending_extra_positions.append(position)
                    
    def _diagram_path(self, image_name: str | None) -> Path | None:
        if not image_name:
            return None
        path = GRID_IMAGE_DIR / image_name
        if not path.exists():
            return None
        return path

    def _build_waypoint_row(self, title: str, key: str, help_image: str | None = None) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: #fbfdff; border: 1px solid #e2e8f0; border-radius: 4px;")
        layout = QHBoxLayout(w)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(8)
        w.setFixedHeight(58)
        
        label_box = QVBoxLayout()
        label_box.setSpacing(1)
        lbl = QLabel(title.rstrip(":"))
        lbl.setStyleSheet("font-weight: bold; font-size: 12px; color: #0f172a; border: none;")
        lbl.setFixedWidth(104)
        diagram = DiagramLabel("DIAGRAM", self._diagram_path(help_image))
        diagram.setStyleSheet("font-weight: bold; font-size: 9px; color: #3978bd; border: none;")
        diagram.setFixedWidth(104)
        label_box.addWidget(lbl)
        label_box.addWidget(diagram)
        layout.addLayout(label_box)
        
        r, phi, z = self.waypoint_inputs[key]
        
        for name, spin in [("Radius", r), ("Phi", phi), ("Height", z)]:
            sl = QVBoxLayout()
            sl.setSpacing(0)
            sl_lbl = QLabel(name)
            sl_lbl.setStyleSheet("color: #64748b; font-size: 10px; font-weight: 800; border: none;")
            spin.setMinimumWidth(72)
            spin.setStyleSheet("border: none; border-bottom: 1px solid #cbd5e1; background: transparent; font-size: 12px;")
            sl.addWidget(sl_lbl)
            sl.addWidget(spin)
            layout.addLayout(sl, 1)
            
        btn = QPushButton("SET")
        primary_button(btn)
        btn.setFixedSize(62, 32)
        btn.clicked.connect(lambda _=False, k=key: self._set_waypoint_from_scanner(k))
        layout.addWidget(btn)
        return w
        
    def _labeled_stat(self, label: str, widget: QWidget) -> QWidget:
        w = QWidget()
        w.setStyleSheet("border: 1px solid #cbd5e1; border-radius: 4px; padding: 4px;")
        w.setFixedHeight(58)
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #64748b; font-size: 10px; border: none;")
        widget.setStyleSheet("border: none; background: transparent; font-weight: bold; color: #334155;")
        layout.addWidget(lbl)
        layout.addWidget(widget)
        return w

    def _build_settings_group(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("border: none;")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        btn = QPushButton("Advanced Settings")
        btn.setStyleSheet("text-align: left; font-weight: bold; background: #f3f4f6; color: #111827; padding: 8px; border-radius: 4px; border: 1px solid #e5e7eb;")
        layout.addWidget(btn)
        
        self.settings_content = QWidget()
        self.settings_content.setVisible(False)
        self.settings_content.setStyleSheet("background: #fbfdff; border: 1px solid #e2e8f0; border-top: none; border-radius: 0 0 4px 4px;")
        btn.clicked.connect(lambda: self.settings_content.setVisible(not self.settings_content.isVisible()))
        
        grid = QGridLayout(self.settings_content)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        
        grid_vars = project.get_project_data().get("grid_vars", {})
        grid_vars = grid_vars if isinstance(grid_vars, dict) else {}
        self.num_points = self._spin(self._gv_float(grid_vars, "num_points", 1000), 1, 100000, "", decimals=0)
        self.az_density = self._spin(self._gv_float(grid_vars, "azimuth_density_ratio", 1.0), 0.05, 20.0, "", decimals=2)
        self.cyl_radius = self._spin(self._gv_float(grid_vars, "cyl_radius_mm", 200.0), 1, 5000, " mm")
        self.cyl_height = self._spin(self._gv_float(grid_vars, "cyl_height_mm", 500.0), 1, 5000, " mm")
        self.phi_min = self._spin(self._gv_float(grid_vars, "phi_min_deg", -170.0), -360, 360, " deg")
        self.phi_max = self._spin(self._gv_float(grid_vars, "phi_max_deg", 170.0), -360, 360, " deg")
        self.bottom_cutoff = self._spin(self._gv_float(grid_vars, "bottom_cutoff_mm", 30.0), 0, 1000, " mm")
        self.delta_theta = self._spin(self._gv_float(grid_vars, "delta_theta_deg", 7.5), 0.1, 90, " deg")
        self.wall_thickness = self._spin(self._gv_float(grid_vars, "wall_thickness_mm", 50.0), 0, 1000, " mm")
        self.cap_fraction = QLineEdit(str(grid_vars.get("cap_fraction") or "Auto"))
        self.p_side = self._spin(self._gv_float(grid_vars, "P_side", 0.5), 0.01, 5.0, "", decimals=2)
        self.p_caps = self._spin(self._gv_float(grid_vars, "P_caps", 0.8), 0.01, 5.0, "", decimals=2)
        self.cap_tol = QLineEdit(str(grid_vars.get("cap_tol_mm") or "Auto"))
        self.az_weight = self._spin(self._gv_float(grid_vars, "azimuth_weight_center_deg", 0.0), -180, 180, " deg")
        self.z_rotation = self._spin(self._gv_float(grid_vars, "z_rotation_deg", 90.0), -360, 360, " deg")
        self.reverse_spiral = QCheckBox("Generate reverse spiral")
        self.reverse_spiral.setChecked(self._gv_bool(grid_vars, "generate_reverse_spiral", True))
        self.flip_poles = QCheckBox("Flip poles")
        self.z_midpoint_zero = QCheckBox("Z midpoint = 0")
        self.flip_poles.setChecked(self._gv_bool(grid_vars, "flip_poles", False))
        self.z_midpoint_zero.setChecked(self._gv_bool(grid_vars, "z_midpoint_zero", True))
        self.snake_start = QComboBox()
        light_combo(self.snake_start)
        self.snake_start.addItems(["up", "down"])
        if str(grid_vars.get("side_snake_start") or "") in {"up", "down"}:
            self.snake_start.setCurrentText(str(grid_vars.get("side_snake_start")))

        fields = [
            ("Cyl Radius", self.cyl_radius), ("Cyl Height", self.cyl_height),
            ("Phi Min", self.phi_min), ("Phi Max", self.phi_max),
            ("Bottom Cutoff", self.bottom_cutoff), ("Delta Theta", self.delta_theta),
            ("Wall Thickness", self.wall_thickness), ("Cap Fraction", self.cap_fraction),
            ("P Side", self.p_side), ("P Caps", self.p_caps),
            ("Cap Tol", self.cap_tol), ("Az Weight Center", self.az_weight),
            ("Z Rotation", self.z_rotation), ("Side Snake Start", self.snake_start),
        ]
        
        for index, (label, widget) in enumerate(fields):
            row = index // 4
            col = index % 4
            grid.addWidget(self._settings_field(label, widget), row, col)

        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 4, 0, 0)
        toggle_row.setSpacing(18)
        for checkbox in (self.reverse_spiral, self.flip_poles, self.z_midpoint_zero):
            checkbox.setStyleSheet(toggle_style() + "QCheckBox { font-weight: 700; }")
            toggle_row.addWidget(checkbox)
        toggle_row.addStretch(1)
        grid.addLayout(toggle_row, 4, 0, 1, 4)
        for col in range(4):
            grid.setColumnStretch(col, 1)
        
        layout.addWidget(self.settings_content)
        return w

    def _settings_field(self, label: str, widget: QWidget) -> QWidget:
        cell = QWidget()
        cell.setStyleSheet("background: #ffffff; border: 1px solid #e2e8f0; border-radius: 4px;")
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(8, 4, 8, 6)
        layout.setSpacing(2)
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #64748b; font-size: 10px; font-weight: 800; border: none;")
        widget.setStyleSheet("border: none; border-bottom: 1px solid #cbd5e1; background: transparent; font-size: 12px;")
        layout.addWidget(lbl)
        layout.addWidget(widget)
        return cell



    def _build_viewer_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        
        btn_style = "QPushButton { border: 1px solid #8bb8e6; border-radius: 3px; padding: 4px 6px; background: white; font-weight: bold; color: #3978bd; }"
        
        rewind = QPushButton("|<")
        rewind.setFixedWidth(32)
        rewind.setStyleSheet(btn_style)
        rewind.clicked.connect(self._rewind)
        step_back = QPushButton("<")
        step_back.setFixedWidth(28)
        step_back.setStyleSheet(btn_style)
        step_back.clicked.connect(self._step_back)
        self.play_button = QPushButton("PLAY")
        self.play_button.setFixedWidth(56)
        primary_button(self.play_button)
        self.play_button.clicked.connect(self.toggle_play)
        step_fwd = QPushButton(">")
        step_fwd.setFixedWidth(28)
        step_fwd.setStyleSheet(btn_style)
        step_fwd.clicked.connect(self._step_fwd)
        
        self.rate = self._spin(600, 1, 5000, "", decimals=0)
        self.rate.setFixedWidth(64)
        self.rate.valueChanged.connect(lambda value: self.engine.set_speed(value))
        
        top = QPushButton("TOP")
        top.setFixedWidth(44)
        top.setStyleSheet(btn_style)
        top.clicked.connect(lambda: self.engine.set_view(90, 0))
        front = QPushButton("FRONT")
        front.setFixedWidth(54)
        front.setStyleSheet(btn_style)
        front.clicked.connect(lambda: self.engine.set_view(0, -90))
        side = QPushButton("SIDE")
        side.setFixedWidth(46)
        side.setStyleSheet(btn_style)
        side.clicked.connect(lambda: self.engine.set_view(0, 0))
        
        self.ortho = QCheckBox("Ortho")
        self.ortho.setStyleSheet(toggle_style() + "QCheckBox { font-weight: bold; }")
        self.ortho.stateChanged.connect(lambda state: self.engine.set_ortho(bool(state)))
        self.bounds = QCheckBox("Bounds")
        self.bounds.setStyleSheet(toggle_style() + "QCheckBox { font-weight: bold; }")
        self.bounds.setChecked(True)
        self.bounds.stateChanged.connect(lambda state: self.engine.set_bounds_visibility(bool(state)))
        self.grid = QCheckBox("Grid")
        self.grid.setStyleSheet(toggle_style() + "QCheckBox { font-weight: bold; }")
        self.grid.setChecked(False)
        self.grid.stateChanged.connect(lambda state: self.engine.set_grid_visibility(bool(state)))
        
        self.rot_angle = self._spin(45, 5, 180, "", decimals=0)
        self.rot_angle.setFixedWidth(54)
        rotate = QPushButton("ROTATE")
        rotate.setFixedWidth(62)
        rotate.setStyleSheet(btn_style)
        rotate.clicked.connect(self.toggle_rotation)
        
        more = QPushButton("MORE")
        more.setFixedWidth(52)
        more.setStyleSheet(btn_style)
        more.clicked.connect(lambda: self._toggle_viewer_more_popup(more))

        for widget in (
            rewind, step_back, self.play_button, step_fwd, QLabel("Rate:"), self.rate,
            top, front, side, self.ortho, self.bounds,
        ):
            if isinstance(widget, QLabel):
                widget.setStyleSheet("font-weight: bold; border: none;")
            row.addWidget(widget)
        row.addSpacing(12)
        for widget in (QLabel("Rot:"), self.rot_angle, rotate, more):
            if isinstance(widget, QLabel):
                widget.setStyleSheet("font-weight: bold; border: none;")
            row.addWidget(widget)
        row.addStretch(1)
        return row

    def _toggle_viewer_more_popup(self, anchor: QWidget) -> None:
        if self.viewer_more_popup is not None:
            self.viewer_more_popup.close()
            self.viewer_more_popup = None
            return

        popup = QFrame(self, Qt.WindowType.Popup)
        popup.setStyleSheet(
            "QFrame { background: white; border: 1px solid #cbd5e1; border-radius: 4px; }"
            "QLabel { color: #64748b; font-size: 10px; font-weight: 800; border: none; }"
            "QCheckBox { border: none; font-weight: 700; }"
            "QSlider { border: none; }"
            "QDoubleSpinBox { border: none; border-bottom: 1px solid #cbd5e1; background: transparent; }"
        )
        layout = QGridLayout(popup)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)

        tail = self._spin(float(self.engine.tail_length), 1, 5000, "", decimals=0)
        tail.valueChanged.connect(self.engine.set_tail_length)
        layout.addWidget(QLabel("Tail Length"), 0, 0)
        layout.addWidget(tail, 0, 1, 1, 2)

        rot_speed = self._spin(float(self.engine.rotation_speed_deg_per_sec), 0.1, 180, " deg/s", decimals=1)
        rot_speed.valueChanged.connect(self.engine.set_rotation_speed)
        layout.addWidget(QLabel("Rot Speed"), 1, 0)
        layout.addWidget(rot_speed, 1, 1, 1, 2)

        fade_hist = QCheckBox("Fade history")
        fade_hist.setChecked(bool(self.engine.use_history_fading))
        fade_hist.stateChanged.connect(lambda state: self.engine.set_history_mode(bool(state)))
        readout = QCheckBox("Readout")
        readout.setChecked(bool(self.engine.show_readout))
        readout.stateChanged.connect(lambda state: self.engine.toggle_readout(bool(state)))
        grid = QCheckBox("Grid")
        grid.setChecked(self.grid.isChecked())
        grid.stateChanged.connect(lambda state: self.grid.setChecked(bool(state)))
        self.grid.stateChanged.connect(lambda state, checkbox=grid: checkbox.setChecked(bool(state)))
        layout.addWidget(fade_hist, 2, 0, 1, 3)
        layout.addWidget(readout, 3, 0, 1, 3)
        layout.addWidget(grid, 4, 0, 1, 3)

        popup.destroyed.connect(lambda _=None: setattr(self, "viewer_more_popup", None))
        popup.adjustSize()
        popup.move(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        popup.show()
        self.viewer_more_popup = popup

    def _spin(self, value: float, min_value: float, max_value: float, suffix: str, decimals: int = 1) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(min_value, max_value)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setSuffix(suffix)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        return spin

    def _gv_float(self, grid_vars: dict, key: str, fallback: float) -> float:
        try:
            value = grid_vars.get(key, fallback)
            if value in (None, ""):
                return fallback
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _gv_bool(self, grid_vars: dict, key: str, fallback: bool) -> bool:
        value = grid_vars.get(key, fallback)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _waypoint(self, key: str) -> tuple[float, float, float]:
        r, phi, z = self.waypoint_inputs[key]
        return r.value(), phi.value(), z.value()

    def _add_extra_position(self, saved: dict | None = None) -> None:
        saved = saved or {}
        name = QLineEdit(str(saved.get("name") or f"point_{len(self.extra_position_rows) + 1}"))
        r = self._spin(float(saved.get("r") or 0.0), -2000, 2000, "")
        phi = self._spin(float(saved.get("phi") or 0.0), -360, 360, "")
        z = self._spin(float(saved.get("z") or 0.0), -2000, 2000, "")

        w = QWidget()
        w.setStyleSheet("background: #fbfdff; border: 1px solid #e2e8f0; border-radius: 4px;")
        l = QHBoxLayout(w)
        l.setContentsMargins(8, 5, 8, 5)
        l.setSpacing(8)
        w.setFixedHeight(58)

        name_box = QVBoxLayout()
        name_box.setSpacing(1)
        name_label = QLabel("Name")
        name_label.setStyleSheet("color: #64748b; font-size: 10px; font-weight: 800; border: none;")
        name.setFixedWidth(88)
        name.setStyleSheet("background: transparent; border: none; border-bottom: 1px solid #cbd5e1; font-weight: bold; font-size: 12px;")
        name_box.addWidget(name_label)
        name_box.addWidget(name)
        l.addLayout(name_box)

        for field_label, spin in (("Radius", r), ("Phi", phi), ("Height", z)):
            spin_box = QVBoxLayout()
            spin_box.setSpacing(0)
            lbl = QLabel(field_label)
            lbl.setStyleSheet("color: #64748b; font-size: 10px; font-weight: 800; border: none;")
            spin.setMinimumWidth(72)
            spin.setStyleSheet("border: none; border-bottom: 1px solid #cbd5e1; background: transparent; font-size: 12px;")
            spin_box.addWidget(lbl)
            spin_box.addWidget(spin)
            l.addLayout(spin_box, 1)

        btn = QPushButton("SET")
        primary_button(btn)
        btn.setFixedSize(62, 32)
        btn.clicked.connect(lambda _=False, row={"r": r, "phi": phi, "z": z}: self._set_extra_position_from_scanner(row))
        l.addWidget(btn)
        
        self.extra_positions_layout.addWidget(w)
        widgets = [w]
        self.extra_position_rows.append({"name": name, "r": r, "phi": phi, "z": z, "widgets": widgets, "set_button": btn})

    def _remove_extra_position(self) -> None:
        if not self.extra_position_rows:
            return
        row = self.extra_position_rows.pop()
        for widget in row["widgets"]:
            widget.setParent(None)

    def _additional_positions(self) -> list[tuple[str, tuple[float, float, float]]]:
        positions = []
        for index, row in enumerate(self.extra_position_rows, start=1):
            name_widget = row["name"]
            r_widget = row["r"]
            phi_widget = row["phi"]
            z_widget = row["z"]
            name = name_widget.text().strip() or f"point_{index}"
            positions.append((name, (r_widget.value(), phi_widget.value(), z_widget.value())))
        return positions

    def _optional_float(self, text: str) -> float | None:
        value = text.strip()
        if not value or value.lower() == "auto":
            return None
        return float(value)

    def _set_waypoint_from_scanner(self, key: str) -> None:
        pos = self.backend.get_position()
        if pos is None:
            QMessageBox.warning(self, "Scanner", "No scanner position is available.")
            return
        r, phi, z = self.waypoint_inputs[key]
        r.setValue(float(pos.r()))
        phi.setValue(float(pos.t()))
        z.setValue(float(pos.z()))

    def _set_extra_position_from_scanner(self, row: dict[str, object]) -> None:
        pos = self.backend.get_position()
        if pos is None:
            QMessageBox.warning(self, "Scanner", "No scanner position is available.")
            return
        row["r"].setValue(float(pos.r()))
        row["phi"].setValue(float(pos.t()))
        row["z"].setValue(float(pos.z()))

    def _output_path(self) -> Path:
        filename = self.output_filename.text().strip() or project.get_grid_filename()
        return project.get_project_dir() / filename

    def generate_and_plan(self) -> None:
        if not self.require_session_folder():
            return
        self.status_label.setText("Generating grid...")
        worker = Worker(self._generate_and_plan_blocking)
        worker.signals.failed.connect(lambda message: QMessageBox.warning(self, "Grid Generation Error", message))
        worker.signals.finished.connect(lambda: self.status_label.setText("Generation finished"))
        self.pool.start(worker)

    def _generate_and_plan_blocking(self) -> None:
        top = self._waypoint("top")
        bottom = self._waypoint("bottom")
        tweeter = self._waypoint("tweeter")
        cap_tol = self._optional_float(self.cap_tol.text())
        if cap_tol is None:
            cap_tol = self.wall_thickness.value()

        generated = generate_measurement_grid(
            cyl_radius_mm=self.cyl_radius.value(),
            cyl_height_mm=self.cyl_height.value(),
            num_points=int(self.num_points.value()),
            wall_thickness_mm=self.wall_thickness.value(),
            bottom_cutoff_mm=self.bottom_cutoff.value(),
            cap_fraction=self._optional_float(self.cap_fraction.text()),
            P_side=self.p_side.value(),
            P_caps=self.p_caps.value(),
            generate_reverse_spiral=self.reverse_spiral.isChecked(),
            z_rotation_deg=self.z_rotation.value(),
            flip_poles=self.flip_poles.isChecked(),
            z_midpoint_zero=self.z_midpoint_zero.isChecked(),
            phi_min_deg=self.phi_min.value(),
            phi_max_deg=self.phi_max.value(),
            azimuth_density_ratio=self.az_density.value(),
            azimuth_weight_center_deg=self.az_weight.value(),
            tweeter_pos=tweeter,
            additional_positions=self._additional_positions(),
            ref_origin_pos=self._waypoint("ref_origin"),
            baffle_bot_l_pos=self._waypoint("baffle_bot_l"),
            baffle_top_l_pos=self._waypoint("baffle_top_l"),
            baffle_top_r_pos=self._waypoint("baffle_top_r"),
            top_crit_pos=top,
            bot_crit_pos=bottom,
        )
        output_path = self._output_path()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        planned = plan_path(
            generated,
            cap_tol_mm=cap_tol,
            output_path=str(output_path),
            delta_theta_deg=self.delta_theta.value(),
            side_snake_start=self.snake_start.currentText(),
            show_replay=False,
        )
        grid_vars = self._grid_vars(output_path.name)
        project.update_grid_vars(grid_vars)
        project.apply_to_config(self.config_file)
        project.save_project()
        self.grid_saved.emit(output_path.name, grid_vars)
        self.generated.emit(planned, str(output_path))

    def _grid_vars(self, filename: str) -> dict:
        return {
            "output_filename": filename,
            "top_r": self._waypoint("top")[0],
            "top_phi": self._waypoint("top")[1],
            "top_z": self._waypoint("top")[2],
            "wp_top_r": self._waypoint("top")[0],
            "wp_top_phi": self._waypoint("top")[1],
            "wp_top_z": self._waypoint("top")[2],
            "bottom_r": self._waypoint("bottom")[0],
            "bottom_phi": self._waypoint("bottom")[1],
            "bottom_z": self._waypoint("bottom")[2],
            "wp_bot_r": self._waypoint("bottom")[0],
            "wp_bot_phi": self._waypoint("bottom")[1],
            "wp_bot_z": self._waypoint("bottom")[2],
            "tweeter_r": self._waypoint("tweeter")[0],
            "tweeter_phi": self._waypoint("tweeter")[1],
            "tweeter_z": self._waypoint("tweeter")[2],
            "wp_tw_r": self._waypoint("tweeter")[0],
            "wp_tw_phi": self._waypoint("tweeter")[1],
            "wp_tw_z": self._waypoint("tweeter")[2],
            "wp_ref_origin_r": self._waypoint("ref_origin")[0],
            "wp_ref_origin_phi": self._waypoint("ref_origin")[1],
            "wp_ref_origin_z": self._waypoint("ref_origin")[2],
            "wp_baffle_bl_r": self._waypoint("baffle_bot_l")[0],
            "wp_baffle_bl_phi": self._waypoint("baffle_bot_l")[1],
            "wp_baffle_bl_z": self._waypoint("baffle_bot_l")[2],
            "wp_baffle_tl_r": self._waypoint("baffle_top_l")[0],
            "wp_baffle_tl_phi": self._waypoint("baffle_top_l")[1],
            "wp_baffle_tl_z": self._waypoint("baffle_top_l")[2],
            "wp_baffle_tr_r": self._waypoint("baffle_top_r")[0],
            "wp_baffle_tr_phi": self._waypoint("baffle_top_r")[1],
            "wp_baffle_tr_z": self._waypoint("baffle_top_r")[2],
            "num_points": int(self.num_points.value()),
            "azimuth_density_ratio": self.az_density.value(),
            "cyl_radius_mm": self.cyl_radius.value(),
            "cyl_height_mm": self.cyl_height.value(),
            "phi_min_deg": self.phi_min.value(),
            "phi_max_deg": self.phi_max.value(),
            "bottom_cutoff_mm": self.bottom_cutoff.value(),
            "delta_theta_deg": self.delta_theta.value(),
            "wall_thickness_mm": self.wall_thickness.value(),
            "cap_fraction": self.cap_fraction.text(),
            "P_side": self.p_side.value(),
            "P_caps": self.p_caps.value(),
            "cap_tol_mm": self.cap_tol.text(),
            "azimuth_weight_center_deg": self.az_weight.value(),
            "z_rotation_deg": self.z_rotation.value(),
            "generate_reverse_spiral": self.reverse_spiral.isChecked(),
            "flip_poles": self.flip_poles.isChecked(),
            "z_midpoint_zero": self.z_midpoint_zero.isChecked(),
            "side_snake_start": self.snake_start.currentText(),
            "user_positions": [
                {
                    "name": row["name"].text().strip(),
                    "r": row["r"].value(),
                    "phi": row["phi"].value(),
                    "z": row["z"].value(),
                }
                for row in self.extra_position_rows
            ],
        }

    def _load_dataframe_on_ui(self, df: pd.DataFrame, path_text: str) -> None:
        path = Path(path_text)
        self.current_viewer_input = df.copy()
        try:
            self.engine.load_data(self.current_viewer_input)
        except Exception as exc:
            if not self._fallback_to_matplotlib_viewer(exc, "generated grid load"):
                logger.exception("Could not load generated grid into viewer")
                QMessageBox.warning(self, "Grid Viewer", str(exc))
                return
        self._redraw_viewer()
        self._update_slider_range()
        self.status_label.setText(f"Loaded {len(df)} points from {path.name}")

    def load_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Grid CSV",
            str(project.get_project_dir()),
            "CSV files (*.csv);;All files (*.*)",
        )
        if path:
            self._load_csv_path(Path(path))

    def _load_csv_path(self, path: Path) -> None:
        self.current_viewer_input = str(path)
        try:
            self.engine.load_data(self.current_viewer_input)
            self._redraw_viewer()
            self._update_slider_range()
            self.status_label.setText(f"Loaded {self.engine.N} points from {path.name}")
        except Exception as exc:
            if self._fallback_to_matplotlib_viewer(exc, "grid CSV load"):
                self._update_slider_range()
                self.status_label.setText(f"Loaded {self.engine.N} points from {path.name}")
                return
            logger.exception("Could not load grid CSV")
            QMessageBox.warning(self, "Load Grid", str(exc))

    def _load_existing_project_grid(self) -> None:
        grid_vars = project.get_project_data().get("grid_vars", {})
        filename = grid_vars.get("output_filename") if isinstance(grid_vars, dict) else None
        path = project.get_project_dir() / str(filename or project.get_grid_filename())
        if path.exists():
            self.output_filename.setText(path.name)
            self._load_csv_path(path)

    def _update_slider_range(self) -> None:
        self.scrub.blockSignals(True)
        self.scrub.setRange(0, max(0, int(self.engine.N) - 1))
        self.scrub.setValue(int(self.engine.curr_idx))
        self.scrub.blockSignals(False)

    def _set_current_index(self, idx: int) -> None:
        try:
            self.engine.set_current_index(idx)
        except Exception as exc:
            self._fallback_to_matplotlib_viewer(exc, "scrub update")

    def _redraw_viewer(self) -> None:
        if self.canvas is not None:
            self.canvas.draw_idle()
        elif hasattr(self.engine, "plotter"):
            try:
                self.engine.plotter.render()
            except Exception as exc:
                self._fallback_to_matplotlib_viewer(exc, "viewer render")

    def _fallback_to_matplotlib_viewer(self, exc: Exception, context: str) -> bool:
        if self.viewer_backend != "pyvista":
            return False
        self._pyvista_error = str(exc)
        logger.exception("PyVista coordinate viewer failed during {}; falling back to Matplotlib", context)
        self.status_label.setText("PyVista viewer failed; using Matplotlib 3D viewer.")
        self._switch_viewer_backend("matplotlib")
        self._show_pyvista_fallback_message()
        return True

    def _show_pyvista_fallback_message(self) -> None:
        if self._pyvista_error is None or self._pyvista_fallback_message_shown:
            return
        self._pyvista_fallback_message_shown = True
        QMessageBox.warning(
            self,
            "PyVista Viewer",
            "Advanced 3D visualisation with PyVista failed, falling back to Matplotlib.\n\n"
            f"{self._pyvista_error}",
        )

    def refresh_from_config(self) -> None:
        requested_backend = self._read_viewer_backend()
        if requested_backend == self.viewer_backend and self._pyvista_error is None:
            return
        self._switch_viewer_backend(requested_backend)

    def _switch_viewer_backend(self, backend: str) -> None:
        try:
            self.engine.pause()
            self.engine.stop_rotation()
            if hasattr(self.engine, "shutdown"):
                self.engine.shutdown()
        except Exception:
            pass
        if self.viewer_more_popup is not None:
            self.viewer_more_popup.close()
            self.viewer_more_popup = None

        previous_input = self.current_viewer_input
        self.viewer_backend = backend
        self.engine = self._create_viewer_engine(backend)
        self._install_viewer_widget()

        if self._pyvista_error is not None and backend == "pyvista":
            self._show_pyvista_fallback_message()

        self.engine.set_speed(self.rate.value())
        self.engine.set_ortho(self.ortho.isChecked())
        self.engine.set_bounds_visibility(self.bounds.isChecked())
        self.engine.set_grid_visibility(self.grid.isChecked())

        if previous_input is not None:
            try:
                self.engine.load_data(previous_input)
            except Exception as exc:
                logger.exception("Could not reload grid after viewer backend switch")
                QMessageBox.warning(self, "Viewer Backend", f"Switched viewer, but could not reload grid:\n\n{exc}")
        self._update_slider_range()
        self._redraw_viewer()

    def shutdown(self) -> None:
        try:
            self.engine.pause()
            self.engine.stop_rotation()
            if hasattr(self.engine, "shutdown"):
                self.engine.shutdown()
        except Exception:
            pass

    def toggle_play(self) -> None:
        try:
            if self.engine.is_playing:
                self.engine.pause()
                self.play_button.setText("Play")
            else:
                self.engine.play()
                self.play_button.setText("Pause")
        except Exception as exc:
            self._fallback_to_matplotlib_viewer(exc, "playback toggle")

    def toggle_rotation(self) -> None:
        try:
            if self.engine.is_rotating:
                self.engine.stop_rotation()
            else:
                self.engine.start_rotation(self.rot_angle.value())
        except Exception as exc:
            self._fallback_to_matplotlib_viewer(exc, "rotation toggle")

    def _rewind(self) -> None:
        try:
            self.engine.rewind()
            self._update_slider_range()
        except Exception as exc:
            self._fallback_to_matplotlib_viewer(exc, "rewind")

    def _step_back(self) -> None:
        try:
            self.engine.step_back()
            self._update_slider_range()
        except Exception as exc:
            self._fallback_to_matplotlib_viewer(exc, "step back")

    def _step_fwd(self) -> None:
        try:
            self.engine.step_fwd()
            self._update_slider_range()
        except Exception as exc:
            self._fallback_to_matplotlib_viewer(exc, "step forward")

    def _sync_viewer_controls(self) -> None:
        if self.scrub.maximum() != max(0, int(self.engine.N) - 1):
            self._update_slider_range()
        elif self.engine.is_playing and self.scrub.value() != self.engine.curr_idx:
            self.scrub.blockSignals(True)
            self.scrub.setValue(int(self.engine.curr_idx))
            self.scrub.blockSignals(False)
        self.play_button.setText("Pause" if self.engine.is_playing else "Play")
