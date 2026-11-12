import os
from pathlib import Path
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from harmonic_drive_qt import project
from harmonic_drive_qt.main_window import MainWindow, NO_SESSION_FOLDER_TEXT
from harmonic_drive_qt.qt_compat import QApplication, QLineEdit


def _app():
    return QApplication.instance() or QApplication([])


def _write_config(path: Path) -> None:
    path.write_text(
        "[sweep]\n"
        "sweep_dur_s = 1.0\n"
        "num_sweeps = 1\n"
        "protect_hpf_hz = None\n"
        "protect_hpf_order = 1\n"
        "protect_hpf_correction = False\n"
        "protect_hpf_corr_db_cap = 12.0\n"
        "\n"
        "[motion_manager]\n"
        "type = CylindricalMeasurementMotionManager\n",
        encoding="utf-8",
    )


def _window_shell(config_file: Path):
    _app()
    window = MainWindow.__new__(MainWindow)
    window.config_file = str(config_file)
    window.project_path = QLineEdit(NO_SESSION_FOLDER_TEXT)
    window.project_name = QLineEdit(project.DEFAULT_PROJECT_NAME)
    window.backend = Mock()
    window.control_pane = Mock()
    window.live_capture = Mock()
    window._rebuild_grid_pane = Mock()
    return window


@pytest.mark.parametrize(
    "app_config, visible",
    [
        ("", True),
        ("[app]\nshow_motion_preview = True\n", True),
        ("[app]\nshow_motion_preview = False\n", False),
    ],
)
def test_motion_preview_menu_defaults_on_and_respects_explicit_choice(tmp_path, app_config, visible):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(app_config, encoding="utf-8")
    window = MainWindow.__new__(MainWindow)
    window.config_file = str(config_file)

    window.menu = window._build_menu()

    assert window.motion_preview_button.isHidden() is not visible


def test_qt_activating_session_folder_does_not_save_project_json(tmp_path):
    config_file = tmp_path / "config.ini"
    session_dir = tmp_path / "speaker_a"
    _write_config(config_file)
    project.set_project_dir(tmp_path / "working", str(config_file))

    window = _window_shell(config_file)

    window.activate_project_context(session_dir, preserve_entered_name=False)

    assert project.get_project_dir() == session_dir.resolve()
    assert window.project_path.text() == str(session_dir.resolve())
    assert window.project_name.text() == "speaker_a"
    assert not list(session_dir.glob("*_project.json"))
    for rel_path in (
        "measurement_set",
        "measurement_set/Distortion",
        "single_measurements",
        "single_measurements/Distortion",
        "logs",
    ):
        assert (session_dir / rel_path).is_dir()
    window.backend.set_project_dir.assert_called_with(session_dir)
    window._rebuild_grid_pane.assert_called_once_with()


def test_qt_save_button_persists_selected_session_with_entered_name(tmp_path):
    config_file = tmp_path / "config.ini"
    session_dir = tmp_path / "speaker_b"
    _write_config(config_file)
    project.set_project_dir(tmp_path / "working", str(config_file))

    window = _window_shell(config_file)
    window.activate_project_context(session_dir, preserve_entered_name=False)
    window.project_name.setText("Speaker B")

    window.save_project_context()

    saved_files = list(session_dir.glob("*_project.json"))
    assert [path.name for path in saved_files] == ["Speaker_B_project.json"]


def test_qt_save_button_does_not_reload_and_reset_flushed_hpf_settings(tmp_path):
    config_file = tmp_path / "config.ini"
    session_dir = tmp_path / "speaker_c"
    _write_config(config_file)
    project.set_project_dir(tmp_path / "working", str(config_file))

    window = _window_shell(config_file)
    window.activate_project_context(session_dir, preserve_entered_name=False)
    window.project_name.setText("Speaker C")
    window.audio_pane = Mock()

    def flush_pending_changes():
        project.update_audio_setup(
            {},
            {
                "sweep_dur_s": "2.5",
                "num_sweeps": "3",
                "protect_hpf_hz": "500",
                "protect_hpf_order": "4",
                "protect_hpf_correction": "True",
                "protect_hpf_corr_db_cap": "9.0",
            },
        )

    window.audio_pane.flush_pending_changes.side_effect = flush_pending_changes

    window.save_project_context()

    parser = project.configparser.ConfigParser()
    parser.read(config_file)
    assert parser.get("sweep", "protect_hpf_hz") == "500"
    assert parser.get("sweep", "protect_hpf_order") == "4"
    saved_file = session_dir / "Speaker_C_project.json"
    content = saved_file.read_text(encoding="utf-8")
    assert '"protect_hpf_hz": "500"' in content
    assert '"protect_hpf_order": "4"' in content


def test_qt_save_button_updates_existing_json_with_flushed_hpf_settings(tmp_path):
    config_file = tmp_path / "config.ini"
    session_dir = tmp_path / "speaker_d"
    _write_config(config_file)
    project.set_project_dir(session_dir, str(config_file))
    project.set_project_name("Speaker D")
    project.update_audio_setup(
        {},
        {
            "sweep_dur_s": "1.0",
            "num_sweeps": "1",
            "protect_hpf_hz": "None",
            "protect_hpf_order": "1",
            "protect_hpf_correction": "False",
            "protect_hpf_corr_db_cap": "12.0",
        },
    )
    project.save_project()

    window = _window_shell(config_file)
    window.project_path.setText(str(session_dir.resolve()))
    window.project_name.setText("Speaker D")
    window.audio_pane = Mock()

    def flush_pending_changes():
        project.update_audio_setup(
            {},
            {
                "sweep_dur_s": "3.0",
                "num_sweeps": "2",
                "protect_hpf_hz": "700",
                "protect_hpf_order": "3",
                "protect_hpf_correction": "True",
                "protect_hpf_corr_db_cap": "8.0",
            },
        )

    window.audio_pane.flush_pending_changes.side_effect = flush_pending_changes

    window.save_project_context()

    content = (session_dir / "Speaker_D_project.json").read_text(encoding="utf-8")
    assert '"protect_hpf_hz": "700"' in content
    assert '"protect_hpf_order": "3"' in content
    assert '"protect_hpf_hz": "None"' not in content


@pytest.mark.parametrize("rebuilds", [1, 2])
def test_rebuilt_controls_update_live_positions_and_3d_progress(tmp_path, monkeypatch, qtbot, rebuilds):
    import threading
    import pandas as pd
    import harmonic_drive_qt.main_window as main_window
    import harmonic_drive_qt.live_capture as live_capture
    from harmonic_drive_qt.qt_compat import QStackedWidget, Signal, QWidget

    class Controls(QWidget):
        measurement_saved = Signal()
        progress_event = Signal(dict)

        def __init__(self, backend, require_session_folder):
            super().__init__()

    monkeypatch.setattr(main_window, "ControlPane", Controls)
    monkeypatch.setattr(live_capture, "_get_viewer_settings", lambda: {})
    window = _window_shell(tmp_path / "config.ini")
    window.left_stack = QStackedWidget()
    qtbot.addWidget(window.left_stack)
    window.control_pane = Controls(window.backend, window.require_session_folder)
    window.left_stack.addWidget(window.control_pane)

    pane = live_capture.LiveCapturePane.__new__(live_capture.LiveCapturePane)
    QWidget.__init__(pane)
    qtbot.addWidget(pane)
    pane._completed_positions = []
    pane.grid_df = pd.DataFrame({
        "r_xy_mm": [100, 100, 100], "phi_deg": [0, 10, 20], "z_mm": [0, 0, 0],
    })
    pane.positions = Mock()
    pane.progress_engine = Mock(N=3)
    pane.progress_canvas = Mock()
    pane.refresh_all = Mock()
    window.live_capture = pane
    window._connect_control_pane_signals()

    for _ in range(rebuilds):
        window._rebuild_control_pane()

    def measure():
        window.control_pane.progress_event.emit({"status": "started", "current": 0, "total": 3})
        for current, angle in enumerate((0, 10), start=1):
            window.control_pane.progress_event.emit({
                "status": "point_complete", "current": current, "total": 3,
                "position": {"r": 100, "phi": angle, "z": 0},
            })
        window.control_pane.progress_event.emit({"status": "finished", "current": 2, "total": 3})

    worker = threading.Thread(target=measure)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    qtbot.waitUntil(lambda: len(pane._completed_positions) == 2)

    assert [pos["phi"] for pos in pane._completed_positions] == [0, 10]
    assert pane.positions.set_data_with_progress.call_args.args[2] == [0, 10]
    assert pane.progress_engine.curr_idx == 1
    assert pane.progress_engine.exact_idx == 1.0
    pane.progress_engine.update_plot.assert_called()
    pane.progress_canvas.draw_idle.assert_called()
    pane.refresh_all.assert_not_called()
    window.control_pane.measurement_saved.emit()
    pane.refresh_all.assert_called_once_with()


def test_live_capture_stream_led_tracks_resource_state(tmp_path, monkeypatch, qtbot):
    import harmonic_drive_qt.live_capture as live_capture
    from nfs.audio import get_audio_meter_state

    config_file = tmp_path / "config.ini"
    config_file.write_text("[app]\ncoord_viewer_backend = matplotlib\n", encoding="utf-8")
    monkeypatch.setattr(live_capture, "_load_grid_dataframe", lambda: None)
    monkeypatch.setattr(live_capture, "_load_grid_points", lambda: [])
    monkeypatch.setattr(live_capture, "_load_latest_ir", lambda: (None, None, None))
    backend = Mock()
    state = dict(get_audio_meter_state(), active=False, stream_open=False)
    backend.get_audio_meter_state.return_value = state
    pane = live_capture.LiveCapturePane(backend, str(config_file))
    qtbot.addWidget(pane)
    try:
        assert pane.stream_status.text() == "Stream closed"
        assert pane.stream_led.property("streamOpen") is False
        state["stream_open"] = True
        qtbot.waitUntil(lambda: pane.stream_status.text() == "Stream open")
        assert pane.stream_led.property("streamOpen") is True
        assert "#22c55e" in pane.stream_led.styleSheet()
        state.update(active=True, stream_open=False)
        qtbot.waitUntil(lambda: pane.stream_status.text() == "Stream closed")
        assert pane.stream_led.property("streamOpen") is False
        assert "#94a3b8" in pane.stream_led.styleSheet()
    finally:
        pane.meter_timer.stop()
        pane.watch_timer.stop()
