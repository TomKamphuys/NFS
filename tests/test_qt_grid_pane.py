import os
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from harmonic_drive_qt import project
from harmonic_drive_qt.grid_pane import GridGeneratorPane
from harmonic_drive_qt.qt_compat import QApplication, QMessageBox


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
        assert pane.advanced_settings_group.title() == "Advanced grid settings"
        assert pane.advanced_settings_group.isCheckable()
        assert not pane.advanced_settings_group.isChecked()
        assert pane.settings_content.isHidden()
        pane.advanced_settings_group.setChecked(True)
        assert not pane.settings_content.isHidden()
        pane.advanced_settings_group.setChecked(False)
        assert pane.settings_content.isHidden()
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
            "cap_tol_mm": "Auto: wall_thickness_mm + 1mm",
        }
    )

    pane = GridGeneratorPane(Mock(), str(config_file))
    try:
        assert [field.text() for field in pane.waypoint_inputs["top"]] == ["123.4", "-12.0", "456.7"]
        assert pane._waypoint("top") == (123.4, -12.0, 456.7)
        assert [field.text() for field in pane.waypoint_inputs["bottom"]] == ["", "", ""]
        assert pane.cap_tol.text() == "Auto"
    finally:
        pane.shutdown()
        pane.sync_timer.stop()
        pane.deleteLater()


def test_grid_generation_uses_advanced_settings_when_waypoints_are_blank(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    _write_config(config_file)
    project.set_project_dir(tmp_path / "speaker_c", str(config_file))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, message: warnings.append((title, message)))

    pane = GridGeneratorPane(Mock(), str(config_file))
    try:
        assert pane._generation_geometry_mode() == "manual"
        assert warnings == [
            (
                "Grid Generation",
                "No waypoints are defined, so the grid will use the cylinder height, cylinder radius, bottom cutoff, and Z midpoint settings from Advanced grid settings.",
            )
        ]
        assert pane._optional_float("Auto: wall_thickness_mm + 1mm") is None
    finally:
        pane.shutdown()
        pane.sync_timer.stop()
        pane.deleteLater()


def test_saved_grid_geometry_uses_waypoints_over_advanced_settings(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    _write_config(config_file)
    project.set_project_dir(tmp_path / "speaker_waypoints", str(config_file))

    pane = GridGeneratorPane(Mock(), str(config_file))
    try:
        pane.cyl_radius.setValue(999.0)
        pane.cyl_height.setValue(888.0)
        pane.bottom_cutoff.setValue(777.0)
        for field, value in zip(pane.waypoint_inputs["top"], (240.0, 10.0, 350.0)):
            field.setText(str(value))
        for field, value in zip(pane.waypoint_inputs["bottom"], (42.0, -20.0, -50.0)):
            field.setText(str(value))

        geometry = pane._generation_geometry_mode()
        assert geometry == "waypoints"
        effective = {
            "cyl_radius_mm": 240.0,
            "cyl_height_mm": 400.0,
            "bottom_cutoff_mm": 42.0,
        }
        grid_vars = pane._grid_vars("grid.csv", effective)

        assert grid_vars["cyl_radius_mm"] == 240.0
        assert grid_vars["cyl_height_mm"] == 400.0
        assert grid_vars["bottom_cutoff_mm"] == 42.0
    finally:
        pane.shutdown()
        pane.sync_timer.stop()
        pane.deleteLater()


def test_grid_pane_accepts_comma_decimal_text_inputs(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    _write_config(config_file)
    project.set_project_dir(tmp_path / "speaker_comma", str(config_file))

    pane = GridGeneratorPane(Mock(), str(config_file))
    try:
        top_r, top_phi, top_z = pane.waypoint_inputs["top"]
        top_r.setText("123,4")
        top_phi.setText("-12,5")
        top_z.setText("456,7")
        pane.cap_fraction.setText("0,25")
        pane.cap_tol.setText("12,5")

        assert pane._waypoint("top") == (123.4, -12.5, 456.7)
        assert pane._optional_float(pane.cap_fraction.text()) == 0.25
        assert pane._optional_float(pane.cap_tol.text()) == 12.5
    finally:
        pane.shutdown()
        pane.sync_timer.stop()
        pane.deleteLater()


def test_grid_generation_requires_waypoints_or_advanced_geometry(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    _write_config(config_file)
    project.set_project_dir(tmp_path / "speaker_d", str(config_file))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, message: warnings.append((title, message)))

    pane = GridGeneratorPane(Mock(), str(config_file))
    try:
        pane.cyl_radius.lineEdit().clear()
        assert pane._generation_geometry_mode() is None
        assert warnings == [
            (
                "Grid Generation",
                "Grid generation needs either Top and Bottom waypoints or cylinder height, cylinder radius, bottom cutoff, and Z midpoint settings from Advanced grid settings.",
            )
        ]
    finally:
        pane.shutdown()
        pane.sync_timer.stop()
        pane.deleteLater()
