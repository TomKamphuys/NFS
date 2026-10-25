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