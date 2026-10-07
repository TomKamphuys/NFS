import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from harmonic_drive_qt.motion_preview import MotionPreviewWindow
from harmonic_drive_qt.qt_compat import QApplication, QMessageBox
from nfs.datatypes import CylindricalPosition
from nfs.motion_simulation import MotionSimulation, RecordingScanner


def test_rotation_samples_do_not_wrap():
    scanner = RecordingScanner(CylindricalPosition(100, -180, 20))
    scanner.angular_move_to(180)
    simulation = MotionSimulation(segments=scanner.segments)
    path = simulation.flatten_cylindrical()
    assert len(path) == 181
    assert path[90] == (100, 0, 20)
    assert path[-1] == (100, 180, 20)
    xyz = MotionPreviewWindow._cartesian(path)
    np.testing.assert_allclose(xyz[:, 90], [100, 0, 20])
    assert simulation.flatten() == [(100, 20)]


def test_combined_and_arc_samples_retain_angles():
    scanner = RecordingScanner(CylindricalPosition(100, 30, 0))
    scanner.move_to(200, 90, 100)
    segment = scanner.segments[-1]
    assert segment.theta[0] == 30
    assert segment.theta[-1] == 90
    assert len(segment.theta) == len(segment.r) == len(segment.z)
    scanner.ccw_arc_move_to(0, np.hypot(200, 100), np.hypot(200, 100))
    assert set(scanner.segments[-1].theta) == {90}


@pytest.mark.parametrize("kind,zone", [
    ("cylindrical", {"r_wall": 50, "z_min": -20, "z_max": 100}),
    ("spherical", {"radius": 50}),
])
def test_view_switch_preserves_playback_and_warnings(monkeypatch, kind, zone):
    app = QApplication.instance() or QApplication([])
    scanner = RecordingScanner(CylindricalPosition(100, 0, 0))
    scanner.angular_move_to(90)
    simulation = MotionSimulation(
        segments=scanner.segments, points=[scanner.get_position()],
        zone_kind=kind, zone=zone,
        unsafe_points=[CylindricalPosition(10, 45, 0)])
    monkeypatch.setattr(MotionPreviewWindow, "_load_simulation", lambda self: simulation)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    window = MotionPreviewWindow("unused.ini")
    try:
        assert window._axes.name == "rectilinear"
        window._slider.setValue(15)
        window._toggle_play()
        window._view_selector.setCurrentIndex(1)
        window._canvas.draw()
        assert window._axes.name == "3d"
        assert window._timer.isActive()
        assert window._slider.value() == 15
        expected = window._cartesian([window._cylindrical_path[15]])
        np.testing.assert_allclose(window._arm_marker.get_data_3d(), expected)
        assert window._unsafe_scatter is not None
        window._toggle_flash()
        assert not window._unsafe_scatter.get_visible()
        window._axes.view_init(elev=10, azim=20)
        window._advance_frame()
        assert window._axes.elev == 10
        window._view_selector.setCurrentIndex(0)
        window._canvas.draw()
        assert window._slider.value() == 18
        assert not window._unsafe_scatter.get_visible()
        assert window._timer.isActive()
    finally:
        window.close()
    assert not window._timer.isActive()
    assert not window._flash_timer.isActive()


def test_trail_shows_latest_ten_measurement_points(monkeypatch):
    app = QApplication.instance() or QApplication([])
    scanner = RecordingScanner(CylindricalPosition(100, 0, 0))
    simulation = MotionSimulation(segments=scanner.segments)
    arrivals = []
    for point in range(14):
        scanner.radial_move_to(200 + point * 10)
        scanner.angular_move_to(point * 5)
        simulation.points.append(scanner.get_position())
        arrivals.append(len(simulation.flatten_cylindrical()) - 1)
    simulation.measurement_sample_indices = arrivals
    monkeypatch.setattr(MotionPreviewWindow, "_load_simulation", lambda self: simulation)
    window = MotionPreviewWindow("unused.ini")
    try:
        for index in (0, 5, arrivals[8], arrivals[9], arrivals[10] - 1,
                      arrivals[10], arrivals[-1], arrivals[3], 0):
            window._slider.setValue(index)
            reached = [arrival for arrival in arrivals if arrival <= index]
            start = reached[-10] if len(reached) >= 10 else 0
            for view in (1, 0):
                window._view_selector.setCurrentIndex(view)
                if view:
                    expected = window._cartesian(window._cylindrical_path[start:index + 1])
                    np.testing.assert_allclose(window._travelled_line.get_data_3d(), expected)
                else:
                    expected = np.array(window._path[start:index + 1]).T
                    np.testing.assert_allclose(window._travelled_line.get_data(), expected)
                assert len(expected[0]) == index - start + 1
    finally:
        window.close()


def test_empty_preview_switches(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MotionPreviewWindow, "_load_simulation", lambda self: MotionSimulation())
    window = MotionPreviewWindow("unused.ini")
    try:
        for index in (1, 0, 1):
            window._view_selector.setCurrentIndex(index)
            window._canvas.draw()
            assert window._status.text() == "No motion"
        window._toggle_play()
        assert not window._timer.isActive()
    finally:
        window.close()