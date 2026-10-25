"""Real widgets, backend, scanner, workers and timers; only hardware is simulated."""

import configparser
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import QMessageBox

from harmonic_drive_qt import project
from harmonic_drive_qt.backend import BackendManager, Worker
from harmonic_drive_qt.main_window import MainWindow
from nfs.audio import MockInterfaceAudio
from nfs.grbl_controller import GrblControllerFactory, GrblControllerMockSimulatedDRO


pytestmark = pytest.mark.gui_smoke


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    config = configparser.ConfigParser()
    config.read(Path(__file__).resolve().parents[1] / "config_default.ini")
    config["grbl_streamer"]["type"] = "MockSimulatedDRO"
    config["grbl_streamer"]["mock_linear_speed_mm_s"] = "1000"
    config["grbl_streamer"]["mock_angular_speed_deg_s"] = "1000"
    config["audio"]["mode"] = "mock_interface"
    config["app"]["coord_viewer_backend"] = "matplotlib"
    config["app"]["auto_hide_left_menu"] = "False"
    config["logging"]["file"] = str(tmp_path / "scanner.log")
    config_file = tmp_path / "config.ini"
    with config_file.open("w", encoding="utf-8") as stream:
        config.write(stream)
    monkeypatch.chdir(tmp_path)
    for name, value in (("_project_dir", tmp_path), ("_project_data", {}), ("_callbacks", [])):
        monkeypatch.setattr(project, name, value)
    monkeypatch.setattr(GrblControllerFactory, "_instance", None)
    monkeypatch.setattr(GrblControllerFactory, "_instance_signature", None)
    backend = BackendManager(str(config_file))
    widget = MainWindow(backend, str(config_file))
    qtbot.addWidget(widget)
    widget.show()
    try:
        yield widget
    finally:
        thread = widget.control_pane._jog_thread
        if thread is not None:
            thread.join(timeout=5)
            assert not thread.is_alive()
        assert QThreadPool.globalInstance().waitForDone(5000)
        widget.close()


def load_scanner(qtbot, window):
    pane = window.control_pane
    assert all(not button.isEnabled() for button in pane._jog_buttons)
    assert not pane.home_button.isEnabled()
    # Startup loads automatically, not through a Load Scanner button.
    worker = Worker(window.backend.load)
    with qtbot.waitSignal(worker.signals.finished, timeout=10000):
        QThreadPool.globalInstance().start(worker)
    assert window.backend.load_warning is None
    assert isinstance(window.backend.scanner._grbl_controller, GrblControllerMockSimulatedDRO)
    assert isinstance(window.backend.nfs._audio, MockInterfaceAudio)
    with qtbot.waitSignal(
        pane.position_timer.timeout, timeout=5000,
        check_params_cb=lambda: all(button.isEnabled() for button in pane._jog_buttons),
    ):
        pass


def position(window):
    pos = window.backend.get_position()
    return (pos.r(), pos.t(), pos.z())


def click_jog(qtbot, window, index, expected):
    button = window.control_pane._jog_buttons[index]
    assert button.isVisible() and button.isEnabled()
    with qtbot.waitSignal(
        window.control_pane.jog_timer.timeout, timeout=5000,
        check_params_cb=lambda: all(b.isEnabled() for b in window.control_pane._jog_buttons),
    ):
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        assert all(not b.isEnabled() for b in window.control_pane._jog_buttons)
    assert position(window) == pytest.approx(expected)


def test_startup_all_jog_directions_repeat_and_home(qtbot, window):
    load_scanner(qtbot, window)
    # Each row has a direction label, six negative steps, six positive steps,
    # and a direction label. Exercise the 1-unit buttons on all three axes.
    for index, expected in [
        (6, (0, -1, 0)), (7, (0, 0, 0)),
        (20, (-1, 0, 0)), (21, (0, 0, 0)),
        (34, (0, 0, -1)), (35, (0, 0, 0)),
        (35, (0, 0, 1)), (35, (0, 0, 2)),
    ]:
        click_jog(qtbot, window, index, expected)
    with qtbot.waitSignal(
        window.control_pane.position_timer.timeout, timeout=5000,
        check_params_cb=lambda: window.backend.is_homed()
        and "#22c55e" in window.control_pane.home_button.styleSheet(),
    ):
        qtbot.mouseClick(window.control_pane.home_button, Qt.MouseButton.LeftButton)
    assert window.backend.is_homed()
    assert position(window) == pytest.approx((0, 0, 0))
    click_jog(qtbot, window, 35, (0, 0, 1))


def test_controller_error_allows_next_jog(qtbot, window, monkeypatch):
    load_scanner(qtbot, window)
    controller = window.backend.scanner._grbl_controller
    original = controller.send_and_wait_for_move_ready
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))

    def fail_move(message):
        raise RuntimeError("Simulated controller failure")

    monkeypatch.setattr(controller, "send_and_wait_for_move_ready", fail_move)
    with qtbot.waitSignal(
        window.control_pane.jog_timer.timeout, timeout=5000,
        check_params_cb=lambda: bool(warnings),
    ):
        qtbot.mouseClick(window.control_pane._jog_buttons[35], Qt.MouseButton.LeftButton)
    assert warnings == ["Simulated controller failure"]
    assert position(window) == pytest.approx((0, 0, 0))
    monkeypatch.setattr(controller, "send_and_wait_for_move_ready", original)
    click_jog(qtbot, window, 35, (0, 0, 1))