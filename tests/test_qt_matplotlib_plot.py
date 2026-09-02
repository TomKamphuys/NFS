import os
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("matplotlib")

from harmonic_drive_qt import live_capture
from harmonic_drive_qt.matplotlib_plot import MatplotlibPlot
from harmonic_drive_qt.qt_compat import QApplication, QComboBox
from harmonic_drive_qt.styles import header_combo


def _app():
    return QApplication.instance() or QApplication([])


def test_header_combo_has_visible_dropdown_affordance():
    _app()
    combo = QComboBox()
    header_combo(combo)

    assert "border: 1px solid #94a3b8" in combo.styleSheet()
    assert "QComboBox::drop-down" in combo.styleSheet()
    assert "spin-chevron-down.svg" in combo.styleSheet()
    assert combo.height() == 24


def test_line_plot_data_and_home_limits():
    _app()
    plot = MatplotlibPlot("Frequency", "Hz", "dB")
    plot.set_data([20, 100, 1000, 20000], [-30, -20, -10, -15], log_x=True, x_range=(20, 20000), y_range=(-40, 0))

    assert plot.axes.get_xscale() == "log"
    assert plot.axes.get_xlim() == pytest.approx((20, 20000))
    assert plot.axes.get_ylim() == pytest.approx((-40, 0))

    plot.axes.set_xlim(100, 1000)
    plot.axes.set_ylim(-30, -10)
    plot.x_range = (100, 1000)
    plot.y_range = (-30, -10)
    plot.reset_zoom()

    assert plot.axes.get_xlim() == pytest.approx((20, 20000))
    assert plot.axes.get_ylim() == pytest.approx((-40, 0))
    formatter = plot.axes.xaxis.get_major_formatter()
    assert formatter(20) == "20"
    assert formatter(1000) == "1k"
    assert formatter(20000) == "20k"
    plot.close()


def test_measurement_positions_use_coloured_scatter():
    _app()
    plot = MatplotlibPlot("Positions", "Azimuth", "Elevation")
    plot.color_points_by_y = True
    plot.set_data([-90, 0, 90], [-30, 0, 30], scatter=True, x_range=(-180, 180), y_range=(-90, 90))

    assert len(plot.axes.collections) == 1
    assert plot.axes.get_xlim() == pytest.approx((-180, 180))
    assert plot.axes.get_ylim() == pytest.approx((-90, 90))
    plot.close()


def test_repaint_blits_cached_plot_without_rerendering_figure():
    app = _app()
    plot = MatplotlibPlot("Frequency", "Hz", "dB")
    plot.resize(800, 320)
    plot.set_data([20, 1000, 20000], [-30, -10, -20], log_x=True, x_range=(20, 20000))
    plot.show()
    app.processEvents()
    plot.draw()
    app.processEvents()

    original_draw = plot.figure.draw
    plot.figure.draw = Mock(wraps=original_draw)
    try:
        for _ in range(8):
            plot.repaint()
            app.processEvents()

        plot.figure.draw.assert_not_called()
    finally:
        plot.figure.draw = original_draw
        plot.close()


def test_selection_zoom_uses_full_drag_box():
    _app()
    plot = MatplotlibPlot("Positions", "Azimuth", "Elevation")
    plot.set_data([-180, 0, 180], [-90, 0, 90], x_range=(-180, 180), y_range=(-90, 90))

    horizontal_press = SimpleNamespace(xdata=-100, ydata=-20, x=100, y=100)
    horizontal_release = SimpleNamespace(xdata=100, ydata=20, x=400, y=120)
    plot._apply_selection(horizontal_press, horizontal_release)
    assert plot.axes.get_xlim() == pytest.approx((-100, 100))
    assert plot.axes.get_ylim() == pytest.approx((-20, 20))

    plot.reset_zoom()
    vertical_press = SimpleNamespace(xdata=-20, ydata=-40, x=100, y=300)
    vertical_release = SimpleNamespace(xdata=20, ydata=40, x=120, y=100)
    plot._apply_selection(vertical_press, vertical_release)
    assert plot.axes.get_xlim() == pytest.approx((-20, 20))
    assert plot.axes.get_ylim() == pytest.approx((-40, 40))
    plot.close()


def test_home_restores_resolved_automatic_limits():
    _app()
    plot = MatplotlibPlot("Impulse", "Time", "Amplitude")
    plot.set_data([0, 1, 2, 3], [-0.5, 0.25, 1.0, -0.25])
    home_x = plot.axes.get_xlim()
    home_y = plot.axes.get_ylim()

    press = SimpleNamespace(xdata=1, ydata=-0.25, x=100, y=300)
    release = SimpleNamespace(xdata=2, ydata=0.5, x=300, y=100)
    plot._apply_selection(press, release)
    plot.reset_zoom()

    assert plot.axes.get_xlim() == pytest.approx(home_x)
    assert plot.axes.get_ylim() == pytest.approx(home_y)
    plot.close()


def test_right_drag_pans_both_axes_and_home_restores_view():
    _app()
    plot = MatplotlibPlot("Positions", "Azimuth", "Elevation")
    plot.set_data([-180, 0, 180], [-90, 0, 90], x_range=(-180, 180), y_range=(-90, 90))
    home_x = plot.axes.get_xlim()
    home_y = plot.axes.get_ylim()
    dx = plot.axes.bbox.width / 4
    dy = plot.axes.bbox.height / 4

    plot._on_button_press(SimpleNamespace(button=3, inaxes=plot.axes, x=100, y=100))
    plot._on_mouse_move(SimpleNamespace(x=100 + dx, y=100 + dy))

    assert plot.axes.get_xlim() == pytest.approx((-270, 90))
    assert plot.axes.get_ylim() == pytest.approx((-135, 45))
    plot._on_button_release(SimpleNamespace(button=3))
    assert plot._pan_start is None

    plot.reset_zoom()
    assert plot.axes.get_xlim() == pytest.approx(home_x)
    assert plot.axes.get_ylim() == pytest.approx(home_y)
    plot.close()


def test_right_drag_pans_log_frequency_axis_by_ratio():
    _app()
    plot = MatplotlibPlot("Frequency", "Hz", "dB")
    plot.set_data([20, 1000, 20000], [-30, -10, -20], log_x=True, x_range=(20, 20000), y_range=(-40, 0))
    dx = plot.axes.bbox.width / 3

    plot._on_button_press(SimpleNamespace(button=3, inaxes=plot.axes, x=100, y=100))
    plot._on_mouse_move(SimpleNamespace(x=100 + dx, y=100))

    assert plot.axes.get_xlim() == pytest.approx((2, 2000))
    assert plot.axes.get_ylim() == pytest.approx((-40, 0))
    plot._on_button_release(SimpleNamespace(button=3))
    plot.close()


def test_measurement_elevation_is_relative_to_grid_midpoint(tmp_path, monkeypatch):
    positions_file = tmp_path / "measurement_positions.csv"
    positions_file.write_text(
        "r_xy_mm,phi_deg,z_mm\n"
        "100,0,100\n"
        "100,90,200\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(live_capture, "_find_latest_measurement_positions_file", lambda: positions_file)
    monkeypatch.setattr(live_capture, "_grid_z_center", lambda: 100.0)

    azimuth, elevation, _cartesian = live_capture._load_measurement_positions()

    assert azimuth == pytest.approx([0, 90])
    assert elevation == pytest.approx([0, 45])


def test_frequency_response_uses_file_nyquist_limit(tmp_path, monkeypatch):
    ir_file = tmp_path / "capture_ir.wav"
    ir_file.touch()
    ir = np.zeros(2048)
    ir[100] = 1.0
    monkeypatch.setattr(live_capture, "_load_latest_ir", lambda: (ir_file, ir, 48000))

    pane = SimpleNamespace(
        backend=SimpleNamespace(get_preview_ir=lambda: None),
        fr_smoothing_fraction=0,
        imp_section=Mock(),
        freq_section=Mock(),
        impulse=Mock(),
        frequency=Mock(),
    )
    live_capture.LiveCapturePane.refresh_ir_plots(pane)

    _args, kwargs = pane.frequency.set_data.call_args
    assert kwargs["x_range"] == (20.0, 24000.0)
    assert max(_args[0]) <= 24000.0
