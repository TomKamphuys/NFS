"""Native Live Capture pane."""

from __future__ import annotations

import configparser
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
from loguru import logger
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from .backend import BackendManager
from .icons import ui_icon
from .qt_compat import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QFrame,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSize,
    QSizePolicy,
    QStyle,
    QTimer,
    QVBoxLayout,
    QWidget,
    Qt,
)
from .styles import light_combo, toggle_style
from .widgets import LevelMeter, LinePlot


LIVE_CAPTURE_CONFIG_SECTION = "live_capture"
PANEL_ORDER_CONFIG_KEY = "panel_order"
VISIBLE_PANELS_CONFIG_KEY = "visible_panels"
FREQUENCY_SMOOTHING_CONFIG_KEY = "frequency_smoothing_fraction"
PANEL_LABELS = [
    "Audio Meters",
    "Measurement Positions",
    "Frequency Response",
    "3D Progress",
    "Impulse Response",
]
DEFAULT_VISIBLE_PANELS = list(PANEL_LABELS)
DEFAULT_PANEL_ORDER = list(PANEL_LABELS)
DEFAULT_FREQUENCY_SMOOTHING_FRACTION = 24
FREQUENCY_SMOOTHING_OPTIONS = {
    0: "None",
    3: "1/3",
    6: "1/6",
    12: "1/12",
    24: "1/24",
}

GRID_SRC = Path(__file__).resolve().parents[1] / "grid"
if str(GRID_SRC) not in sys.path:
    sys.path.insert(0, str(GRID_SRC))

from coord_viewer_core import CoordViewerEngine  # noqa: E402


def _project_dir() -> Path:
    try:
        from harmonic_drive import project

        return project.get_project_dir()
    except Exception:
        return Path.cwd()


def _normalize_decimal_text(value: str) -> str:
    return value.strip().replace(",", ".")


def _project_frd_db_offset() -> float | None:
    try:
        from harmonic_drive import project

        stage5_vars = project.get_project_data().get("stage5_vars")
        if not isinstance(stage5_vars, dict):
            return None
        value = stage5_vars.get("frd_db_offset")
        return None if value is None else float(_normalize_decimal_text(str(value)))
    except (TypeError, ValueError):
        return None
    except Exception:
        return None


def _find_latest_measurement_positions_file() -> Path | None:
    root = _project_dir()
    candidates = [root / "measurement_positions.csv"]
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def _count_measurement_rows(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            return max(0, sum(1 for _ in handle) - 1)
    except Exception:
        return 0


def _load_measurement_positions() -> tuple[np.ndarray | None, np.ndarray | None, list[tuple[float, float, float]]]:
    path = _find_latest_measurement_positions_file()
    if path is None or _count_measurement_rows(path) == 0:
        return None, None, []
    try:
        data = np.loadtxt(path, delimiter=",", skiprows=1)
        data = np.atleast_2d(data)
        r = data[:, 0]
        phi = data[:, 1]
        z = data[:, 2]
        z_center = _grid_z_center()
        elevation = np.degrees(np.arctan2(z - z_center, r))
        azimuth = phi
        cartesian = []
        for radius, angle_deg, height in zip(r, phi, z):
            angle = math.radians(float(angle_deg))
            cartesian.append(
                (
                    float(radius) * math.cos(angle),
                    float(radius) * math.sin(angle),
                    float(height),
                )
            )
        return azimuth, elevation, cartesian
    except Exception as exc:
        logger.error(f"Error loading measurement positions: {exc}")
        return None, None, []


def _grid_z_center() -> float:
    grid_df = _load_grid_dataframe()
    if grid_df is not None and "z_mm" in grid_df.columns and len(grid_df):
        try:
            return float((grid_df["z_mm"].max() + grid_df["z_mm"].min()) / 2.0)
        except Exception:
            pass
    try:
        from harmonic_drive import project

        grid_vars = project.get_project_data().get("grid_vars")
        if isinstance(grid_vars, dict):
            top = grid_vars.get("wp_top_z", grid_vars.get("top_z"))
            bottom = grid_vars.get("wp_bot_z", grid_vars.get("bottom_z"))
            if top is not None and bottom is not None:
                return (float(top) + float(bottom)) / 2.0
    except Exception:
        pass
    return 0.0


def _find_grid_file() -> Path | None:
    root = _project_dir()
    try:
        from harmonic_drive import project

        grid_name = project.get_grid_filename()
        if grid_name:
            path = root / grid_name
            if path.exists():
                return path
    except Exception:
        pass
    candidates = sorted(root.glob("*grid*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _load_grid_points() -> list[tuple[float, float, float]]:
    path = _find_grid_file()
    if path is None:
        return []
    try:
        df = pd.read_csv(path)
        required = {"r_xy_mm", "phi_deg", "z_mm"}
        if not required.issubset(df.columns):
            return []
        points = []
        for r, phi, z in zip(df["r_xy_mm"], df["phi_deg"], df["z_mm"]):
            angle = math.radians(float(phi))
            points.append((float(r) * math.cos(angle), float(r) * math.sin(angle), float(z)))
        return points
    except Exception as exc:
        logger.debug(f"Could not load grid points for Qt progress cloud: {exc}")
        return []


def _load_grid_dataframe() -> pd.DataFrame | None:
    path = _find_grid_file()
    if path is None:
        return None
    try:
        df = pd.read_csv(path)
        required = {"r_xy_mm", "phi_deg", "z_mm"}
        if not required.issubset(df.columns):
            return None
        return df
    except Exception as exc:
        logger.debug(f"Could not load grid dataframe for Qt progress viewer: {exc}")
        return None


def _find_latest_ir_file() -> Path | None:
    root = _project_dir()
    dirs = [
        root / "measurement_set",
        root / "single_measurements",
        root / "Recordings",
    ]
    files: list[Path] = []
    for directory in dirs:
        if directory.exists():
            files.extend(
                path for path in directory.glob("*_ir.wav")
                if "dist" not in path.name.lower()
            )
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def _load_latest_ir() -> tuple[Path | None, np.ndarray | None, int | None]:
    path = _find_latest_ir_file()
    if path is None:
        return None, None, None
    try:
        data, fs = sf.read(path)
        if data.ndim > 1:
            data = data[:, 0]
        return path, np.asarray(data, dtype=float), int(fs)
    except Exception as exc:
        logger.error(f"Error loading IR file {path}: {exc}")
        return path, None, None


def _smooth_fractional_octave(freqs: np.ndarray, mag_db: np.ndarray, fraction: int) -> np.ndarray:
    if len(freqs) == 0 or fraction <= 0:
        return mag_db
    half_width = 2 ** (1 / (2 * fraction))
    linear_mag = 10 ** (mag_db / 20)
    low = np.searchsorted(freqs, freqs / half_width, side="left")
    high = np.searchsorted(freqs, freqs * half_width, side="right")
    cumulative = np.concatenate(([0.0], np.cumsum(linear_mag)))
    sums = cumulative[high] - cumulative[low]
    counts = np.maximum(1, high - low)
    return 20 * np.log10((sums / counts) + 1e-12)


def _auto_db_range(values: np.ndarray, *, headroom_db: float = 5.0, span_db: float = 50.0) -> tuple[float, float]:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return (-span_db, 0.0)
    ymax = float(np.max(finite)) + headroom_db
    return ymax - span_db, ymax


def _auto_waveform_range(values: np.ndarray, *, headroom_fraction: float = 0.12) -> tuple[float, float]:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return (-1.0, 1.0)
    peak = max(float(np.max(np.abs(finite))), 1e-6)
    limit = peak * (1.0 + headroom_fraction)
    return -limit, limit


class LiveSection(QFrame):
    def __init__(
        self,
        title: str,
        move_callback,
        home_callback=None,
        parent=None,
        config_label: str | None = None,
        visibility_callback=None,
        header_widget: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_label = config_label or title
        self.move_callback = move_callback
        self.home_callback = home_callback
        self.visibility_callback = visibility_callback
        self._drag_start_y: int | None = None
        self._enlarge_dialog: QDialog | None = None
        self._maximized = False
        self._square_mode = False
        self.setObjectName("PlotCard")
        self.setAcceptDrops(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("PlotHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header.setStyleSheet("QFrame#PlotHeader { background-color: #ffffff; border: none; }")
        header.setFixedHeight(34)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 0, 10, 0)
        header_layout.setSpacing(8)

        handle = QLabel("::")
        handle.setStyleSheet("background: transparent; color: #64748b; font-weight: 900; border: none;")
        header_layout.addWidget(handle)

        up = QPushButton()
        down = QPushButton()
        up.setIcon(ui_icon("arrow-up"))
        down.setIcon(ui_icon("arrow-down"))
        up.setToolTip("Move plot up")
        down.setToolTip("Move plot down")
        up.clicked.connect(lambda: self.move_callback(self, -1))
        down.clicked.connect(lambda: self.move_callback(self, 1))
        header_layout.addWidget(up)
        header_layout.addWidget(down)

        label = QLabel(title)
        label.setStyleSheet("background: transparent; color: #0f172a; font-weight: bold; font-size: 13px; border: none;")
        header_layout.addWidget(label)

        self.detail_label = QLabel("")
        self.detail_label.setStyleSheet("background: transparent; color: #64748b; font-size: 11px; border: none;")
        header_layout.addWidget(self.detail_label, 1)
        header_layout.addStretch(1)
        if header_widget is not None:
            header_layout.addWidget(header_widget)

        home = QPushButton()
        enlarge = QPushButton()
        float_button = QPushButton()
        self.toggle_button = QPushButton()
        icon_map = (
            (home, "zoom-home", "Reset zoom"),
            (enlarge, "maximize", "Enlarge in place"),
            (float_button, "float", "Float in separate window"),
            (self.toggle_button, "chevron-up", "Collapse plot"),
        )
        for button, icon, tooltip in icon_map:
            button.setIcon(ui_icon(icon))
            button.setIconSize(QSize(17, 17))
            button.setToolTip(tooltip)
        for button in (up, down, home, enlarge, float_button, self.toggle_button):
            button.setFixedSize(26, 24)
            button.setStyleSheet(
                "QPushButton { background: transparent; border: 0; color: #3978bd; "
                "font-weight: 900; padding: 0; font-size: 10px; }"
                "QPushButton:hover { background: #eef6ff; }"
            )
        home.clicked.connect(self.reset_home)
        enlarge.clicked.connect(self.toggle_enlarged)
        float_button.clicked.connect(self.float_view)
        self.toggle_button.clicked.connect(self.toggle_collapsed)
        header_layout.addWidget(home)
        header_layout.addWidget(enlarge)
        header_layout.addWidget(float_button)
        header_layout.addWidget(self.toggle_button)

        separator = QFrame()
        separator.setObjectName("PlotHeaderSeparator")
        separator.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        separator.setFixedHeight(1)
        separator.setStyleSheet("QFrame#PlotHeaderSeparator { background-color: #e2e8f0; border: none; }")

        self.content = QWidget()
        self.content.setStyleSheet("border: none;")
        layout.addWidget(header)
        layout.addWidget(separator)
        layout.addWidget(self.content)

    def set_detail(self, text: str | None) -> None:
        self.detail_label.setText(text or "")

    def set_square_mode(self, enabled: bool = True) -> None:
        self._square_mode = enabled
        self._apply_square_size()
        QTimer.singleShot(0, self._apply_square_size)

    def reset_home(self) -> None:
        for plot in self.content.findChildren(LinePlot):
            plot.reset_zoom()
        if self.home_callback is not None:
            self.home_callback()

    def toggle_enlarged(self) -> None:
        self._maximized = not self._maximized
        if self._square_mode:
            self._apply_square_size()
            return
        for plot in self.content.findChildren(LinePlot):
            base = int(plot.property("baseMinimumHeight") or plot.minimumHeight())
            if not plot.property("baseMinimumHeight"):
                plot.setProperty("baseMinimumHeight", base)
            plot.setMinimumHeight(base + (220 if self._maximized else 0))
        self.updateGeometry()

    def float_view(self) -> None:
        if self._enlarge_dialog is not None:
            self._enlarge_dialog.raise_()
            return
        section_layout = self.layout()
        section_layout.removeWidget(self.content)
        dialog = QDialog(self)
        dialog.setWindowTitle("Enlarged View")
        dialog.resize(980, 760)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(10, 10, 10, 10)
        self.content.setParent(dialog)
        self.content.setVisible(True)
        dialog_layout.addWidget(self.content)
        self._enlarge_dialog = dialog
        resized_canvases = []
        for canvas in self.content.findChildren(FigureCanvas):
            resized_canvases.append((canvas, canvas.minimumSize(), canvas.maximumSize()))
            canvas.setFixedSize(720, 720)

        def restore() -> None:
            for canvas, minimum, maximum in resized_canvases:
                canvas.setMinimumSize(minimum)
                canvas.setMaximumSize(maximum)
            dialog_layout.removeWidget(self.content)
            self.content.setParent(self)
            section_layout.addWidget(self.content)
            self._enlarge_dialog = None

        dialog.finished.connect(restore)
        dialog.show()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_square_size()

    def _apply_square_size(self) -> None:
        if not self._square_mode or not self.content.isVisible():
            return
        target_side = 835 if self._maximized else 646
        available_width = self.width() - 18 if self.width() > 64 else target_side
        side = max(420, min(target_side, available_width))
        self.content.setMinimumHeight(side)
        self.content.setMaximumHeight(side)
        for canvas in self.content.findChildren(FigureCanvas):
            canvas.setMinimumHeight(max(320, side - 64))
            canvas.setMaximumSize(16777215, 16777215)
            canvas.draw_idle()

    def toggle_collapsed(self) -> None:
        visible = self.content.isVisible()
        self.content.setVisible(not visible)
        self.toggle_button.setText("v" if visible else "^")
        if self.visibility_callback is not None:
            self.visibility_callback()

    def set_collapsed(self, collapsed: bool) -> None:
        self.content.setVisible(not collapsed)
        self.toggle_button.setText("v" if collapsed else "^")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_y = int(event.globalPosition().y())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_start_y is None:
            super().mouseMoveEvent(event)
            return
        if not event.buttons() & Qt.MouseButton.LeftButton:
            self._drag_start_y = None
            super().mouseMoveEvent(event)
            return
        parent = self.parentWidget()
        if parent is not None:
            siblings = [section for section in parent.findChildren(LiveSection) if section.parentWidget() is parent]
            siblings.sort(key=lambda section: section.geometry().top())
            if self in siblings:
                index = siblings.index(self)
                pointer_y = int(event.globalPosition().y())
                if index > 0:
                    previous = siblings[index - 1]
                    if pointer_y < previous.mapToGlobal(previous.rect().center()).y():
                        self.move_callback(self, -1)
                if index < len(siblings) - 1:
                    next_section = siblings[index + 1]
                    if pointer_y > next_section.mapToGlobal(next_section.rect().center()).y():
                        self.move_callback(self, 1)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_start_y = None
        super().mouseReleaseEvent(event)


class LiveCapturePane(QWidget):
    def __init__(self, backend: BackendManager, config_file: str, parent=None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.config_file = config_file
        self.visible_active = True
        self.last_measurement_mtime = 0.0
        self.last_ir_mtime = 0.0
        self.grid_points: list[tuple[float, float, float]] = []
        self.grid_df: pd.DataFrame | None = None
        self.fr_smoothing_fraction = 24
        self.viewer_backend = self._read_viewer_backend()
        self._pyvista_error: str | None = None
        self._pyvista_fallback_message_shown = False
        self.progress_engine = self._create_progress_engine(self.viewer_backend)
        self.progress_canvas = None
        self.progress_widget: QWidget | None = None
        self.progress_viewer_layout: QVBoxLayout | None = None
        self.progress_rotate_button: QPushButton | None = None
        self.sections_by_label: dict[str, LiveSection] = {}
        self._loading_layout_settings = False
        self._build_ui()
        if self._pyvista_error is not None:
            QTimer.singleShot(0, self._show_pyvista_fallback_message)

        self.meter_timer = QTimer(self)
        self.meter_timer.setInterval(100)
        self.meter_timer.timeout.connect(self.refresh_meters)
        self.meter_timer.start()

        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(1000)
        self.watch_timer.timeout.connect(self.refresh_if_changed)
        self.watch_timer.start()
        self.refresh_all()

    def _read_viewer_backend(self) -> str:
        parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        parser.read(self.config_file)
        backend = parser.get("app", "coord_viewer_backend", fallback="matplotlib").strip().lower()
        return backend if backend in {"matplotlib", "pyvista"} else "matplotlib"

    def _create_progress_engine(self, backend: str):
        if backend == "pyvista":
            try:
                from coord_viewer_core_pyvista import CoordViewerPyVista  # noqa: PLC0415

                self._pyvista_error = None
                return CoordViewerPyVista()
            except Exception as exc:
                self._pyvista_error = str(exc)
                logger.exception("Could not create PyVista live progress viewer; falling back to Matplotlib")
                self.viewer_backend = "matplotlib"
        return CoordViewerEngine()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 0, 12, 12)
        root.setSpacing(10)
        self.setStyleSheet("background-color: #ffffff;")
        
        header = QHBoxLayout()
        title = QLabel("Live Capture")
        title.setStyleSheet("font-size: 20px; font-weight: 800; color: #000000; border: none;")
        self.smoothing = QComboBox()
        light_combo(self.smoothing)
        for fraction, label in FREQUENCY_SMOOTHING_OPTIONS.items():
            self.smoothing.addItem(label, fraction)
        self.smoothing.setFixedWidth(82)
        panel_order, visible_panels, smoothing_fraction = self._load_layout_settings()
        self._set_smoothing_fraction(smoothing_fraction)
        self.smoothing.currentIndexChanged.connect(self._on_smoothing_changed)
        
        header.addWidget(title)
        header.addStretch(1)
        root.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll, 1)

        content_root = QWidget()
        self.sections_layout = QVBoxLayout(content_root)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(10)
        scroll.setWidget(content_root)
        self.sections: list[LiveSection] = []

        meters_section, m_content = self._add_section("Audio Meters")
        meters_layout = QGridLayout(m_content)
        meters_layout.setContentsMargins(12, 12, 12, 12)
        meters_layout.setSpacing(8)
        self.meters = [
            LevelMeter("Mic Input CH 0"),
            LevelMeter("Speaker Output CH 0"),
            LevelMeter("Loopback Input CH 1"),
            LevelMeter("Loopback Output CH 1"),
        ]
        for meter, fill, border in (
            (self.meters[0], "#fff6fb", "#fbcfe8"),
            (self.meters[1], "#f1f7ff", "#bfdbfe"),
            (self.meters[2], "#fff6fb", "#fbcfe8"),
            (self.meters[3], "#f1f7ff", "#bfdbfe"),
        ):
            meter.set_tone(fill, border)
        for index, meter in enumerate(self.meters):
            meters_layout.addWidget(meter, index // 2, index % 2)

        self.pos_section, p_content = self._add_section("Measurement Positions")
        p_layout = QVBoxLayout(p_content)
        self.positions = LinePlot("Measurement Positions (Azimuth vs Elevation)", "Azimuth (degrees)", "Elevation (degrees)")
        self.positions.setMinimumHeight(240)
        self.positions.scatter = True
        self.positions.color_points_by_y = True
        p_layout.addWidget(self.positions)

        smoothing_field = QWidget()
        smoothing_field.setStyleSheet("background: transparent; border: none;")
        smoothing_layout = QHBoxLayout(smoothing_field)
        smoothing_layout.setContentsMargins(0, 0, 0, 0)
        smoothing_layout.setSpacing(5)
        smoothing_label = QLabel("Smooth")
        smoothing_label.setStyleSheet("color: #475569; font-weight: 700; font-size: 11px; border: none;")
        smoothing_layout.addWidget(smoothing_label)
        smoothing_layout.addWidget(self.smoothing)
        self.freq_section, f_content = self._add_section("Frequency Response", header_widget=smoothing_field)
        f_layout = QVBoxLayout(f_content)
        self.frequency = LinePlot("Frequency Response", "Frequency (Hz)", "Magnitude (dBFS)")
        self.frequency.setMinimumHeight(325)
        f_layout.addWidget(self.frequency)
        
        self.imp_section, i_content = self._add_section("Impulse Response")
        i_layout = QVBoxLayout(i_content)
        self.impulse = LinePlot("Impulse Response", "Time (ms)", "Amplitude")
        self.impulse.setMinimumHeight(286)
        i_layout.addWidget(self.impulse)

        prog_section, pr_content = self._add_section("Measurement Progress", self._reset_progress_view, config_label="3D Progress")
        pr_layout = QVBoxLayout(pr_content)
        pr_layout.addLayout(self._build_progress_view_controls())
        self._install_progress_viewer(pr_layout)
        prog_section.set_square_mode(True)
        self._apply_layout_settings(panel_order, visible_panels)
        self.sections_layout.addStretch(1)

    def _install_progress_viewer(self, layout: QVBoxLayout) -> None:
        self.progress_viewer_layout = layout
        self.progress_engine.toggle_readout(False)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)
        if self.viewer_backend == "pyvista":
            widget = self.progress_engine
            widget.setMinimumSize(320, 320)
            widget.setMaximumSize(16777215, 16777215)
            self.progress_canvas = None
        else:
            self.progress_engine.fig.subplots_adjust(top=0.98, bottom=0.02, left=0.02, right=0.98)
            try:
                self.progress_engine.ax.set_box_aspect((1, 1, 1))
            except Exception:
                pass
            widget = FigureCanvas(self.progress_engine.fig)
            widget.setMinimumSize(320, 320)
            widget.setMaximumSize(16777215, 16777215)
            self.progress_canvas = widget
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(widget, 1)
        self.progress_widget = widget

    def _build_progress_view_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        btn_style = (
            "QPushButton { border: 1px solid #8bb8e6; border-radius: 3px; "
            "padding: 4px 6px; background: white; font-weight: bold; color: #3978bd; }"
        )

        top = QPushButton("TOP")
        top.setFixedWidth(44)
        top.setStyleSheet(btn_style)
        top.clicked.connect(lambda: self._call_progress_engine("top view", self.progress_engine.set_view, 90, 0))
        front = QPushButton("FRONT")
        front.setFixedWidth(54)
        front.setStyleSheet(btn_style)
        front.clicked.connect(lambda: self._call_progress_engine("front view", self.progress_engine.set_view, 0, -90))
        side = QPushButton("SIDE")
        side.setFixedWidth(46)
        side.setStyleSheet(btn_style)
        side.clicked.connect(lambda: self._call_progress_engine("side view", self.progress_engine.set_view, 0, 0))

        ortho = QCheckBox("Ortho")
        ortho.setStyleSheet(toggle_style() + "QCheckBox { font-weight: bold; }")
        ortho.stateChanged.connect(lambda state: self._call_progress_engine("ortho toggle", self.progress_engine.set_ortho, bool(state)))
        bounds = QCheckBox("Bounds")
        bounds.setStyleSheet(toggle_style() + "QCheckBox { font-weight: bold; }")
        bounds.setChecked(True)
        bounds.stateChanged.connect(lambda state: self._call_progress_engine("bounds toggle", self.progress_engine.set_bounds_visibility, bool(state)))
        grid = QCheckBox("Grid")
        grid.setStyleSheet(toggle_style() + "QCheckBox { font-weight: bold; }")
        grid.setChecked(False)
        self.progress_engine.set_grid_visibility(False)
        grid.stateChanged.connect(lambda state: self._call_progress_engine("grid toggle", self.progress_engine.set_grid_visibility, bool(state)))

        rot_speed = self._spin(5.0, 0.1, 180.0, " deg/s", decimals=1)
        rot_speed.setFixedWidth(88)
        rot_speed.valueChanged.connect(lambda value: self._call_progress_engine("rotation speed", self.progress_engine.set_rotation_speed, value))
        self.progress_rotate_button = QPushButton("ROTATE")
        self.progress_rotate_button.setFixedWidth(62)
        self.progress_rotate_button.setStyleSheet(btn_style)
        self.progress_rotate_button.clicked.connect(self._toggle_progress_rotation)

        for widget in (
            top, front, side, ortho, bounds, grid, QLabel("Rot:"), rot_speed, self.progress_rotate_button
        ):
            if isinstance(widget, QLabel):
                widget.setStyleSheet("font-weight: bold; border: none;")
            row.addWidget(widget)
        row.addStretch(1)
        return row

    def _spin(self, value: float, min_value: float, max_value: float, suffix: str, decimals: int = 1) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(min_value, max_value)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setSuffix(suffix)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        return spin

    def _toggle_progress_rotation(self) -> None:
        try:
            if self.progress_engine.is_rotating:
                self.progress_engine.stop_rotation()
                if self.progress_rotate_button is not None:
                    self.progress_rotate_button.setText("ROTATE")
            else:
                self.progress_engine.start_rotation(45.0)
                if self.progress_rotate_button is not None:
                    self.progress_rotate_button.setText("STOP")
        except Exception as exc:
            self._fallback_to_matplotlib_progress_viewer(exc, "rotation toggle")

    def _call_progress_engine(self, context: str, callback, *args) -> None:
        try:
            callback(*args)
        except Exception as exc:
            self._fallback_to_matplotlib_progress_viewer(exc, context)

    def _add_section(
        self,
        title: str,
        home_callback=None,
        config_label: str | None = None,
        header_widget: QWidget | None = None,
    ) -> tuple["LiveSection", QWidget]:
        section = LiveSection(
            title,
            self._move_section,
            home_callback,
            config_label=config_label,
            visibility_callback=self._save_layout_settings,
            header_widget=header_widget,
        )
        self.sections.append(section)
        self.sections_by_label[section.config_label] = section
        self.sections_layout.addWidget(section)
        return section, section.content

    def _reset_progress_view(self) -> None:
        try:
            self.progress_engine.set_view(30, -45)
            if hasattr(self.progress_engine, "_set_axes_equal"):
                self.progress_engine._set_axes_equal()
            if self.progress_canvas is not None:
                self.progress_canvas.draw_idle()
        except Exception as exc:
            logger.debug(f"Could not reset progress view: {exc}")

    def _move_section(self, section: "LiveSection", direction: int) -> None:
        if section not in self.sections:
            return
        index = self.sections.index(section)
        new_index = max(0, min(len(self.sections) - 1, index + direction))
        if new_index == index:
            return
        self.sections.pop(index)
        self.sections.insert(new_index, section)
        self.sections_layout.removeWidget(section)
        self.sections_layout.insertWidget(new_index, section)
        self._save_layout_settings()

    def _on_smoothing_changed(self) -> None:
        self.fr_smoothing_fraction = int(self.smoothing.currentData() or 0)
        self.refresh_ir_plots()
        self._save_layout_settings()

    def _set_smoothing_fraction(self, fraction: int) -> None:
        target = fraction if fraction in FREQUENCY_SMOOTHING_OPTIONS else DEFAULT_FREQUENCY_SMOOTHING_FRACTION
        for index in range(self.smoothing.count()):
            if self.smoothing.itemData(index) == target:
                self.smoothing.setCurrentIndex(index)
                self.fr_smoothing_fraction = target
                return
        self.fr_smoothing_fraction = DEFAULT_FREQUENCY_SMOOTHING_FRACTION

    def _load_layout_settings(self) -> tuple[list[str], set[str], int]:
        parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        parser.read(self.config_file)
        configured_order: list[str] = []
        if parser.has_option(LIVE_CAPTURE_CONFIG_SECTION, PANEL_ORDER_CONFIG_KEY):
            configured_order = [
                item.strip()
                for item in parser.get(LIVE_CAPTURE_CONFIG_SECTION, PANEL_ORDER_CONFIG_KEY).split(",")
                if item.strip()
            ]
        panel_order = [label for label in configured_order if label in PANEL_LABELS]
        panel_order.extend(label for label in DEFAULT_PANEL_ORDER if label not in panel_order)

        configured_visible: list[str] = []
        if parser.has_option(LIVE_CAPTURE_CONFIG_SECTION, VISIBLE_PANELS_CONFIG_KEY):
            configured_visible = [
                item.strip()
                for item in parser.get(LIVE_CAPTURE_CONFIG_SECTION, VISIBLE_PANELS_CONFIG_KEY).split(",")
                if item.strip()
            ]
        else:
            configured_visible = DEFAULT_VISIBLE_PANELS
        visible_panels = {label for label in configured_visible if label in PANEL_LABELS}

        try:
            smoothing_fraction = parser.getint(
                LIVE_CAPTURE_CONFIG_SECTION,
                FREQUENCY_SMOOTHING_CONFIG_KEY,
                fallback=DEFAULT_FREQUENCY_SMOOTHING_FRACTION,
            )
        except ValueError:
            smoothing_fraction = DEFAULT_FREQUENCY_SMOOTHING_FRACTION
        return panel_order, visible_panels, max(0, smoothing_fraction)

    def _apply_layout_settings(self, panel_order: list[str], visible_panels: set[str]) -> None:
        self._loading_layout_settings = True
        try:
            ordered_sections = [
                self.sections_by_label[label]
                for label in panel_order
                if label in self.sections_by_label
            ]
            ordered_sections.extend(section for section in self.sections if section not in ordered_sections)
            for section in ordered_sections:
                self.sections_layout.removeWidget(section)
            self.sections = ordered_sections
            for index, section in enumerate(self.sections):
                section.set_collapsed(section.config_label not in visible_panels)
                self.sections_layout.insertWidget(index, section)
        finally:
            self._loading_layout_settings = False

    def _save_layout_settings(self) -> None:
        if self._loading_layout_settings:
            return
        parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        parser.optionxform = str  # type: ignore[assignment]
        parser.read(self.config_file)
        if not parser.has_section(LIVE_CAPTURE_CONFIG_SECTION):
            parser.add_section(LIVE_CAPTURE_CONFIG_SECTION)

        panel_order = [section.config_label for section in self.sections if section.config_label in PANEL_LABELS]
        visible_panels = [
            section.config_label
            for section in self.sections
            if section.config_label in PANEL_LABELS and section.content.isVisible()
        ]
        parser.set(LIVE_CAPTURE_CONFIG_SECTION, PANEL_ORDER_CONFIG_KEY, ", ".join(panel_order))
        parser.set(LIVE_CAPTURE_CONFIG_SECTION, VISIBLE_PANELS_CONFIG_KEY, ", ".join(visible_panels))
        parser.set(LIVE_CAPTURE_CONFIG_SECTION, FREQUENCY_SMOOTHING_CONFIG_KEY, str(max(0, int(self.fr_smoothing_fraction))))
        with open(self.config_file, "w", encoding="utf-8") as handle:
            parser.write(handle)

    def set_active(self, active: bool) -> None:
        self.visible_active = active
        if active:
            self.meter_timer.start()
            self.watch_timer.start()
            self.refresh_all()
        else:
            self.meter_timer.stop()
            self.watch_timer.stop()

    def shutdown(self) -> None:
        self.visible_active = False
        for timer in (self.meter_timer, self.watch_timer):
            try:
                timer.stop()
            except Exception:
                pass
        try:
            self.progress_engine.stop_rotation()
            if hasattr(self.progress_engine, "shutdown"):
                self.progress_engine.shutdown()
        except Exception:
            pass

    def _channel_labels(self) -> list[str]:
        parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        parser.read(self.config_file)

        def channel(key: str, fallback: int) -> int:
            try:
                return parser.getint("audio", key, fallback=fallback)
            except ValueError:
                return fallback

        return [
            f"Mic Input CH {channel('in_ch_mic', 1)}",
            f"Speaker Output CH {channel('out_ch_spkr', 0)}",
            f"Loopback Input CH {channel('in_ch_loop', 0)}",
            f"Loopback Output CH {channel('out_ch_ref', 1)}",
        ]

    def refresh_meters(self) -> None:
        if not self.visible_active:
            return
        state = self.backend.get_audio_meter_state()
        labels = self._channel_labels()
        meter_data = [
            state["inputs"][1],
            state["outputs"][0],
            state["inputs"][0],
            state["outputs"][1],
        ]
        for widget, label, meter in zip(self.meters, labels, meter_data):
            widget.set_values(
                label,
                meter.get("rms_dbfs", -120.0),
                meter.get("peak_dbfs", -120.0),
                meter.get("clip", False),
            )

    def refresh_if_changed(self) -> None:
        if not self.visible_active:
            return
        measurement_file = _find_latest_measurement_positions_file()
        measurement_mtime = measurement_file.stat().st_mtime if measurement_file else 0.0
        if measurement_mtime != self.last_measurement_mtime:
            self.last_measurement_mtime = measurement_mtime
            self.refresh_positions()

        ir_file = _find_latest_ir_file()
        ir_mtime = ir_file.stat().st_mtime if ir_file else 0.0
        if ir_mtime != self.last_ir_mtime:
            self.last_ir_mtime = ir_mtime
            self.refresh_ir_plots()

    def refresh_all(self) -> None:
        self.grid_points = _load_grid_points()
        self.grid_df = _load_grid_dataframe()
        self.refresh_progress_viewer(0)
        self.refresh_positions()
        self.refresh_ir_plots()
        self.refresh_meters()

    def refresh_positions(self) -> None:
        azimuth, elevation, measured_points = _load_measurement_positions()
        measured_count = len(measured_points)
        self.refresh_progress_viewer(measured_count)

        if azimuth is None or elevation is None:
            self.positions.clear_data("No measurement positions available", "Waiting for measurement positions...")
            return
        self.positions.set_data(
            azimuth,
            elevation,
            title="Measurement Positions (Azimuth vs Elevation)",
            scatter=True,
            x_range=(-180, 180),
            y_range=(-90, 90),
        )

    def refresh_progress_viewer(self, measured_count: int) -> None:
        try:
            if self.grid_df is not None and int(self.progress_engine.N) != len(self.grid_df):
                self.progress_engine.load_data(self.grid_df)
            elif self.grid_df is None and measured_count:
                azimuth, _elevation, measured_points = _load_measurement_positions()
                if measured_points:
                    rows = []
                    for x, y, z in measured_points:
                        r = math.sqrt(x * x + y * y)
                        phi = math.degrees(math.atan2(y, x))
                        rows.append({"r_xy_mm": r, "phi_deg": phi, "z_mm": z})
                    self.progress_engine.load_data(pd.DataFrame(rows))
            if self.progress_engine.N > 0:
                index = max(0, min(int(self.progress_engine.N) - 1, measured_count - 1))
                self.progress_engine.curr_idx = index
                self.progress_engine.exact_idx = float(index)
                self.progress_engine.update_plot()
            if self.progress_canvas is not None:
                self.progress_canvas.draw_idle()
        except Exception as exc:
            if not self._fallback_to_matplotlib_progress_viewer(exc, "progress refresh"):
                logger.debug(f"Could not refresh Qt coord viewer progress: {exc}")

    def _fallback_to_matplotlib_progress_viewer(self, exc: Exception, context: str) -> bool:
        if self.viewer_backend != "pyvista" or self.progress_viewer_layout is None:
            return False
        self._pyvista_error = str(exc)
        logger.exception("PyVista live progress viewer failed during {}; falling back to Matplotlib", context)
        try:
            self.progress_engine.stop_rotation()
            if hasattr(self.progress_engine, "shutdown"):
                self.progress_engine.shutdown()
        except Exception:
            pass
        if self.progress_widget is not None:
            self.progress_viewer_layout.removeWidget(self.progress_widget)
            self.progress_widget.setParent(None)
            self.progress_widget.deleteLater()
            self.progress_widget = None

        self.viewer_backend = "matplotlib"
        self.progress_engine = CoordViewerEngine()
        self.progress_engine.toggle_readout(False)
        self.progress_engine.set_bounds_visibility(True)
        self.progress_engine.set_grid_visibility(False)
        self.progress_canvas = None
        self._install_progress_viewer(self.progress_viewer_layout)
        self._show_pyvista_fallback_message()
        try:
            if self.grid_df is not None:
                self.progress_engine.load_data(self.grid_df)
                self.progress_engine.update_plot()
            if self.progress_canvas is not None:
                self.progress_canvas.draw_idle()
        except Exception:
            logger.exception("Could not reload live progress viewer after PyVista fallback")
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

    def refresh_ir_plots(self) -> None:
        latest_file, ir, fs = _load_latest_ir()
        title_name = latest_file.name if latest_file is not None else None
        latest_mtime = latest_file.stat().st_mtime if latest_file is not None else 0.0
        preview = self.backend.get_preview_ir()
        preview_created_at = (
            float(preview.get("preview_created_at") or 0.0)
            if isinstance(preview, dict)
            else 0.0
        )
        if isinstance(preview, dict) and preview_created_at >= latest_mtime:
            preview_ir = preview.get("ir_linear")
            if preview_ir is None:
                preview_ir = preview.get("ir_full")
            preview_fs = preview.get("fs")
            if preview_ir is not None and preview_fs is not None:
                ir = np.asarray(preview_ir, dtype=float)
                fs = int(preview_fs)
                title_name = str(preview.get("name") or "Test Sweep Preview")
        if ir is None or fs is None:
            self.imp_section.set_detail("Waiting for impulse response...")
            self.freq_section.set_detail("Waiting for frequency response...")
            self.impulse.clear_data("No impulse response available", "Impulse Response")
            self.frequency.clear_data("No frequency response available", "Frequency Response")
            return
        title_name = title_name or "current capture"

        zoom_ms = 15.0
        zoom_samples = max(1, int((zoom_ms / 1000.0) * fs))
        peak_idx = int(np.argmax(np.abs(ir)))
        start = max(0, peak_idx - int(zoom_samples / 4))
        end = min(len(ir), start + zoom_samples)
        ir_zoom = ir[start:end]
        time_axis = np.arange(len(ir_zoom)) / fs * 1000.0
        self.impulse.set_data(
            time_axis,
            ir_zoom,
            title="Impulse Response",
            y_range=_auto_waveform_range(ir_zoom),
            y_axis_mode="symmetric_dbfs",
        )

        n_fft = 2 ** int(np.ceil(np.log2(max(1, len(ir)))))
        fr = np.fft.rfft(ir, n=n_fft)
        freqs = np.fft.rfftfreq(n_fft, d=1 / fs)
        mag_db = 20 * np.log10(np.abs(fr) + 1e-12)
        frd_db_offset = _project_frd_db_offset()
        if frd_db_offset is not None:
            mag_db = mag_db + frd_db_offset
        valid = freqs > 0
        fraction = int(self.fr_smoothing_fraction or 0)
        plot_freqs = freqs[valid]
        smoothed = _smooth_fractional_octave(plot_freqs, mag_db[valid], fraction)
        audible = (plot_freqs >= 20.0) & (plot_freqs <= 20000.0)
        smooth_label = "unsmoothed" if fraction <= 0 else f"1/{fraction} Oct Smoothed"
        self.imp_section.set_detail(f"Zoomed: {title_name}")
        if frd_db_offset is not None:
            self.frequency.y_label = "Magnitude (dB SPL)"
            self.freq_section.set_detail(f"{smooth_label}, calibrated {frd_db_offset:+.2f} dB: {title_name}")
        else:
            self.frequency.y_label = "Magnitude (dBFS)"
            self.freq_section.set_detail(f"{smooth_label}: {title_name}")
        self.frequency.set_data(
            plot_freqs[audible],
            smoothed[audible],
            title="Frequency Response",
            log_x=True,
            x_range=(20, 20000),
            y_range=_auto_db_range(smoothed[audible]),
        )
