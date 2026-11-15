import os
import threading
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from harmonic_drive_qt.control_pane import ControlPane
from harmonic_drive_qt.qt_compat import QApplication, QMessageBox


@pytest.fixture
def pane(tmp_path):
    app = QApplication.instance() or QApplication([])
    backend = Mock(config_file=str(tmp_path / "config.ini"))
    backend.is_scanner_loaded.return_value = False
    backend.get_position.return_value = None
    backend.get_state.return_value = None
    backend.is_homed.return_value = False
    widget = ControlPane(backend)
    widget.position_timer.stop()
    widget.progress_timer.stop()
    yield widget
    widget.jog_timer.stop()
    widget.close()
    widget.deleteLater()


def test_jog_buttons_enable_after_scanner_load(pane):
    pane.refresh_position()
    assert all(not button.isEnabled() for button in pane._jog_buttons)
    assert not pane._jog_in_progress
    pane.backend.is_scanner_loaded.return_value = True
    pane.refresh_position()
    assert all(button.isEnabled() for button in pane._jog_buttons)
    pane.backend.is_scanner_loaded.return_value = False
    pane.refresh_position()
    assert all(not button.isEnabled() for button in pane._jog_buttons)
    assert all(not button.isEnabled() for button in pane._control_buttons)


def test_detector_messages_cross_to_gui_thread(pane, qtbot):
    from nfs.capture_detection.events import DetectorEvent
    event = DetectorEvent('preparing', 'Loopback reference', 'Capturing reference', total=3, duration_s=5)
    thread = threading.Thread(target=lambda: pane.detector_event.emit(event))
    thread.start()
    thread.join()
    qtbot.waitUntil(lambda: pane._detector_dialog is not None and pane._detector_dialog.isVisible())
    assert pane._detector_dialog.thread() == pane.thread()
    pane._apply_progress_event({'status': 'finished', 'current': 0, 'total': 1})
    assert not pane._detector_dialog.isVisible()


def test_failure_on_last_point_still_displays_stopped(pane):
    pane._apply_progress_event({'status': 'finished', 'current': 1, 'total': 1, 'stopped_early': True})
    assert pane.progress.format().startswith('Stopped')


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("loaded_at_finish", [False, True])
def test_jog_click_busy_and_completion(pane, monkeypatch, fail, loaded_at_finish):
    release = threading.Event()
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)

    def jog(method, value):
        assert release.wait(5)
        if fail:
            raise RuntimeError("Jog failed")

    pane.backend.jog.side_effect = jog
    pane.backend.is_scanner_loaded.return_value = True
    pane.refresh_position()
    try:
        pane._jog_buttons[0].click()
        assert pane._jog_in_progress
        pane.refresh_position()
        assert all(not button.isEnabled() for button in pane._jog_buttons)
        pane._run_jog("move_up", 1)
    finally:
        release.set()
        if pane._jog_thread is not None:
            pane._jog_thread.join(timeout=5)
    assert pane._jog_done
    pane.backend.jog.assert_called_once_with("rotate_cw", 120)
    pane.backend.is_scanner_loaded.return_value = loaded_at_finish
    pane._check_jog_finished()
    assert not pane._jog_in_progress
    assert all(button.isEnabled() == loaded_at_finish for button in pane._jog_buttons)
    assert warning.call_count == int(fail)


def test_jog_without_scanner_does_not_start(pane):
    pane._run_jog("move_up", 1)
    pane.backend.jog.assert_not_called()
    assert not pane._jog_in_progress


def test_active_recapture_readout_survives_progress_timer(pane, monkeypatch):
    original = [dict(r=900., p=0., z=0.), dict(r=901., p=5., z=0.)]
    def reload():
        pane.grid_readout_points = list(original)
    monkeypatch.setattr(pane, '_reload_grid_readout_points', reload)
    points = [dict(r=410., phi=15., z=110.), dict(r=420., phi=30., z=120.)]
    pane.set_recapture_points(points)
    pane._apply_progress_event(dict(status='point_started', current=2, total=2, recapture=True, points=points))
    assert pane.grid_readout_points == [dict(r=410., p=15., z=110.), dict(r=420., p=30., z=120.)]
    assert '901.0' not in pane.coord_readout.text()
    assert '420.0' in pane.coord_readout.text()
    # Backend polling carries the active queue too, rather than reverting to the CSV.
    pane._apply_progress_event(dict(status='finished', current=2, total=2, recapture=True, points=points))
    assert pane.grid_readout_points[0]['r'] == 410.
    pane._apply_progress_event(dict(status='started', current=0, total=2))
    assert pane.grid_readout_points == original
