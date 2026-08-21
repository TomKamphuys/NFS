import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from harmonic_drive_qt.live_capture import LiveCapturePane
from harmonic_drive_qt.qt_compat import QApplication, QVBoxLayout, QWidget
from harmonic_drive_qt.widgets import LinePlot, MatplotlibLinePlot


def _app():
    return QApplication.instance() or QApplication([])


def test_matplotlib_setting_bypasses_native_line_plot(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[app]\nuse_matplotlib_live_plots = True\n",
        encoding="utf-8",
    )
    pane = LiveCapturePane.__new__(LiveCapturePane)
    pane.config_file = str(config_file)
    pane.live_plot_backend = pane._read_live_plot_backend()

    positions = pane._create_line_plot("Positions", "Azimuth", "Elevation")
    frequency = pane._create_line_plot("Frequency", "Frequency", "Magnitude")
    impulse = pane._create_line_plot("Impulse", "Time", "Amplitude")

    assert pane.live_plot_backend == "matplotlib"
    assert all(isinstance(plot, MatplotlibLinePlot) for plot in (positions, frequency, impulse))
    assert not any(isinstance(plot, LinePlot) for plot in (positions, frequency, impulse))


def test_native_live_plots_remain_the_default(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text("[app]\n", encoding="utf-8")
    pane = LiveCapturePane.__new__(LiveCapturePane)
    pane.config_file = str(config_file)
    pane.live_plot_backend = pane._read_live_plot_backend()

    plot = pane._create_line_plot("Frequency", "Frequency", "Magnitude")

    assert pane.live_plot_backend == "pyside6"
    assert isinstance(plot, LinePlot)


def test_backend_sync_replaces_all_three_native_plots(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[app]\nuse_matplotlib_live_plots = False\n",
        encoding="utf-8",
    )
    pane = LiveCapturePane.__new__(LiveCapturePane)
    pane.config_file = str(config_file)
    pane.live_plot_backend = "pyside6"
    containers = [QWidget(), QWidget(), QWidget()]
    pane.positions_layout, pane.frequency_layout, pane.impulse_layout = (
        QVBoxLayout(container) for container in containers
    )
    pane.positions = LinePlot("Positions")
    pane.frequency = LinePlot("Frequency")
    pane.impulse = LinePlot("Impulse")
    for layout, plot in (
        (pane.positions_layout, pane.positions),
        (pane.frequency_layout, pane.frequency),
        (pane.impulse_layout, pane.impulse),
    ):
        layout.addWidget(plot)

    config_file.write_text(
        "[app]\nuse_matplotlib_live_plots = True\n",
        encoding="utf-8",
    )
    pane._sync_live_plot_backend()

    assert pane.live_plot_backend == "matplotlib"
    assert all(
        isinstance(plot, MatplotlibLinePlot)
        for plot in (pane.positions, pane.frequency, pane.impulse)
    )


def test_matplotlib_line_plot_applies_log_scale_and_ranges():
    _app()
    plot = MatplotlibLinePlot("Frequency Response", "Frequency (Hz)", "Magnitude (dBFS)")

    plot.set_data(
        [20, 1000, 20000],
        [-30, -12, -24],
        log_x=True,
        x_range=(20, 20000),
        y_range=(-40, 0),
    )

    assert plot.ax.get_xscale() == "log"
    assert plot.ax.get_xlim() == pytest.approx((20, 20000))
    assert plot.ax.get_ylim() == pytest.approx((-40, 0))
    assert len(plot.ax.lines) == 1
