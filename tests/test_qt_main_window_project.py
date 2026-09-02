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
