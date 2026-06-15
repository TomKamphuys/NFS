import os
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from harmonic_drive import project
from harmonic_drive_qt.grid_pane import GridGeneratorPane
from harmonic_drive_qt.qt_compat import QApplication


def _app():
    return QApplication.instance() or QApplication([])


def _write_config(path):
    path.write_text(
        "[app]\n"
        "coord_viewer_backend = matplotlib\n"
        "\n"
        "[sweep]\n"
        "sweep_dur_s = 1.0\n"
        "num_sweeps = 1\n"
        "\n"
        "[motion_manager]\n"
        "type = CylindricalMeasurementMotionManager\n",
        encoding="utf-8",
    )


def test_grid_pane_starts_with_blank_waypoints_and_no_default_grid_load(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    session_dir = tmp_path / "speaker_a"
    _write_config(config_file)
    project.set_project_dir(session_dir, str(config_file))
    (session_dir / project.get_grid_filename()).write_text("r_xy_mm,phi_deg,z_mm\n", encoding="utf-8")
    loaded_paths = []
    monkeypatch.setattr(GridGeneratorPane, "_load_csv_path", lambda self, path: loaded_paths.append(path))

    pane = GridGeneratorPane(Mock(), str(config_file))
    try:
        assert [field.text() for field in pane.waypoint_inputs["top"]] == ["", "", ""]
        assert [field.text() for field in pane.waypoint_inputs["bottom"]] == ["", "", ""]
        assert [field.text() for field in pane.waypoint_inputs["tweeter"]] == ["", "", ""]
        assert pane._waypoint("top") is None
        assert loaded_paths == []
    finally:
        pane.shutdown()
        pane.sync_timer.stop()
        pane.deleteLater()


def test_grid_pane_restores_waypoints_only_from_saved_grid_vars(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    _write_config(config_file)
    project.set_project_dir(tmp_path / "speaker_b", str(config_file))
    project.update_grid_vars(
        {
            "wp_top_r": 123.4,
            "wp_top_phi": -12.0,
            "wp_top_z": 456.7,
        }
    )

    pane = GridGeneratorPane(Mock(), str(config_file))
    try:
        assert [field.text() for field in pane.waypoint_inputs["top"]] == ["123.4", "-12.0", "456.7"]
        assert pane._waypoint("top") == (123.4, -12.0, 456.7)
        assert [field.text() for field in pane.waypoint_inputs["bottom"]] == ["", "", ""]
    finally:
        pane.shutdown()
        pane.sync_timer.stop()
        pane.deleteLater()
