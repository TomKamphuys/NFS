"""
Optional 2D motion-preview window.

This window lets the operator *see* the complete planned motion of the arm --
every interpolated move, not just the measurement end points -- together with
the configured no-fly (keep-out) zone. A movie-player style slider (with
play/pause) scrubs through the motion in time so a single transition can be
inspected in detail and the operator can confirm the arm never enters the
protected volume.

The heavy lifting (building the configured motion manager and reconstructing the
path) lives in :mod:`nfs.motion_simulation`, which is GUI-free and unit tested.
"""
from __future__ import annotations

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle, Wedge

from loguru import logger

from nfs.datatypes import CylindricalPosition
from nfs.motion_simulation import MotionSimulation, simulate_motion

from .qt_compat import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
)


class MotionPreviewWindow(QMainWindow):
    """A standalone window that animates the planned motion in the (r, z) plane."""

    # Milliseconds between animation frames while playing.
    _PLAY_INTERVAL_MS = 30
    # How many path samples to advance per animation frame.
    _STEP_PER_FRAME = 3
    # Milliseconds between on/off toggles of the unsafe-point flash.
    _FLASH_INTERVAL_MS = 500

    def __init__(
        self,
        config_file: str,
        parent=None,
        start: CylindricalPosition | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_file = config_file
        self._start = start
        self.setWindowTitle("Motion Preview")
        self.resize(900, 800)

        self._timer = QTimer(self)
        self._timer.setInterval(self._PLAY_INTERVAL_MS)
        self._timer.timeout.connect(self._advance_frame)

        # A separate, always-running timer makes the unsafe measurement points
        # flash so they cannot be overlooked.
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(self._FLASH_INTERVAL_MS)
        self._flash_timer.timeout.connect(self._toggle_flash)
        self._flash_on = True
        self._unsafe_scatter = None

        self._simulation = self._load_simulation()
        self._path = self._simulation.flatten()

        self._build_ui()
        self._draw_static()
        # Start with the slider at zero so no trajectory appears travelled yet;
        # the full path is shown gray until the user plays/scrubs.
        self._update_position(0)
        self._announce_unsafe_points()

    # -- setup ------------------------------------------------------------- #
    def _load_simulation(self) -> MotionSimulation:
        try:
            return simulate_motion(self.config_file, start=self._start)
        except Exception as exc:  # pragma: no cover - defensive against bad config
            logger.error(f"Motion preview simulation failed: {exc}")
            return MotionSimulation()

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        self.setCentralWidget(central)

        self._figure = Figure(facecolor="white")
        self._axes = self._figure.add_subplot(111)
        self._canvas = FigureCanvasQTAgg(self._figure)
        layout.addWidget(self._canvas, 1)

        controls = QHBoxLayout()
        self._play_button = QPushButton("Play")
        self._play_button.setFixedWidth(90)
        self._play_button.clicked.connect(self._toggle_play)
        controls.addWidget(self._play_button)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(max(0, len(self._path) - 1))
        self._slider.valueChanged.connect(self._on_slider)
        controls.addWidget(self._slider, 1)

        self._status = QLabel("")
        self._status.setMinimumWidth(220)
        controls.addWidget(self._status)

        layout.addLayout(controls)

        # A prominent banner that only appears when unsafe points were found.
        self._warning_label = QLabel("")
        self._warning_label.setWordWrap(True)
        self._warning_label.setStyleSheet(
            "QLabel { background-color: #fee2e2; color: #991b1b; "
            "border: 1px solid #dc2626; border-radius: 4px; padding: 6px; "
            "font-weight: bold; }"
        )
        self._warning_label.setVisible(False)
        layout.addWidget(self._warning_label)

    # -- drawing ----------------------------------------------------------- #
    def _draw_static(self) -> None:
        axes = self._axes
        axes.clear()
        axes.set_title("Planned motion (r, z plane)", fontsize=10)
        axes.set_xlabel("r (mm)")
        axes.set_ylabel("z (mm)")
        axes.set_aspect("equal", adjustable="datalim")
        axes.grid(True, color="#e5e7eb", linewidth=0.8)

        self._draw_zone(axes)

        if self._path:
            rs = [p[0] for p in self._path]
            zs = [p[1] for p in self._path]
            # Full path drawn faintly as context.
            axes.plot(rs, zs, color="#cbd5e1", linewidth=1.0, zorder=1)
            # Measurement end points.
            pr = [p.r() for p in self._simulation.points]
            pz = [p.z() for p in self._simulation.points]
            axes.scatter(pr, pz, s=10, color="#2563eb", zorder=2,
                         label="Measurement points")
        elif not self._simulation.unsafe_points:
            axes.text(0.5, 0.5, "No motion to display", transform=axes.transAxes,
                      ha="center", va="center", color="#6b7280")

        self._draw_unsafe_points(axes)

        # Dynamic artists: travelled path and the arm marker.
        (self._travelled_line,) = axes.plot(
            [], [], color="#16a34a", linewidth=2.0, zorder=3, label="Travelled")
        (self._arm_marker,) = axes.plot(
            [], [], marker="o", markersize=9, color="#dc2626", zorder=4,
            label="Arm")

        handles, labels = axes.get_legend_handles_labels()
        if handles:
            axes.legend(loc="upper right", fontsize=8)
        self._canvas.draw_idle()

    def _draw_unsafe_points(self, axes) -> None:
        """Draw the measurement points that fall inside the no-fly zone."""
        unsafe = self._simulation.unsafe_points
        if not unsafe:
            self._unsafe_scatter = None
            return
        ur = [p.r() for p in unsafe]
        uz = [p.z() for p in unsafe]
        self._unsafe_scatter = axes.scatter(
            ur, uz, s=140, marker="X", color="#dc2626",
            edgecolors="#7f1d1d", linewidths=1.5, zorder=6,
            label="Unsafe points (in no-fly zone)")
        # Start the attention-grabbing flash.
        self._flash_timer.start()

    def _toggle_flash(self) -> None:
        """Blink the unsafe-point markers on and off."""
        if self._unsafe_scatter is None:
            return
        self._flash_on = not self._flash_on
        self._unsafe_scatter.set_visible(self._flash_on)
        self._canvas.draw_idle()

    def _announce_unsafe_points(self) -> None:
        """Show the warning banner and a pop-up when unsafe points were found."""
        unsafe = self._simulation.unsafe_points
        if not unsafe:
            return
        count = len(unsafe)
        noun = "point" if count == 1 else "points"
        if self._simulation.aborted:
            consequence = (
                "With the current settings the scan will be ABORTED before any "
                "motion, to avoid a collision."
            )
        else:
            consequence = (
                f"With the current settings {'this' if count == 1 else 'these'} "
                f"{noun} will be SKIPPED during the scan."
            )
        message = (
            f"{count} measurement {noun} lie inside the no-fly zone. "
            f"{consequence} Review the no-fly zone settings and the measurement "
            f"grid."
        )
        self._warning_label.setText(f"\u26a0  {message}")
        self._warning_label.setVisible(True)
        QMessageBox.warning(self, "Unsafe measurement points", message)

    def _draw_zone(self, axes) -> None:
        kind = self._simulation.zone_kind
        zone = self._simulation.zone
        if kind == "cylindrical":
            r_wall = zone["r_wall"]
            z_min = zone["z_min"]
            z_max = zone["z_max"]
            axes.add_patch(Rectangle(
                (0.0, z_min), r_wall, z_max - z_min,
                facecolor="#fca5a5", edgecolor="#dc2626", alpha=0.35,
                hatch="//", zorder=0, label="No-fly zone"))
        elif kind == "spherical":
            radius = zone["radius"]
            # Only r >= 0 is physically reachable; draw the right half-disc.
            axes.add_patch(Wedge(
                (0.0, 0.0), radius, -90, 90,
                facecolor="#fca5a5", edgecolor="#dc2626", alpha=0.35,
                hatch="//", zorder=0, label="No-fly zone"))

    # -- interaction ------------------------------------------------------- #
    def _toggle_play(self) -> None:
        if self._timer.isActive():
            self._stop_play()
        else:
            if not self._path:
                return
            # Restart from the beginning if we are at the end.
            if self._slider.value() >= self._slider.maximum():
                self._slider.setValue(0)
            self._timer.start()
            self._play_button.setText("Pause")

    def _stop_play(self) -> None:
        self._timer.stop()
        self._play_button.setText("Play")

    def _advance_frame(self) -> None:
        value = self._slider.value() + self._STEP_PER_FRAME
        if value >= self._slider.maximum():
            self._slider.setValue(self._slider.maximum())
            self._stop_play()
        else:
            self._slider.setValue(value)

    def _on_slider(self, value: int) -> None:
        self._update_position(value)

    def _update_position(self, index: int) -> None:
        if not self._path:
            self._status.setText("No motion")
            return
        index = max(0, min(index, len(self._path) - 1))
        rs = [p[0] for p in self._path[: index + 1]]
        zs = [p[1] for p in self._path[: index + 1]]
        self._travelled_line.set_data(rs, zs)
        cur_r, cur_z = self._path[index]
        self._arm_marker.set_data([cur_r], [cur_z])
        self._status.setText(
            f"Sample {index + 1}/{len(self._path)}   r={cur_r:.1f} mm  z={cur_z:.1f} mm"
        )
        self._canvas.draw_idle()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._stop_play()
        self._flash_timer.stop()
        super().closeEvent(event)
