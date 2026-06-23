import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from harmonic_drive_qt.qt_compat import QApplication, QComboBox, QLineEdit
from harmonic_drive_qt.settings_dialog import SettingsDialog
from harmonic_drive.config_editor import DISPLAY_LABELS


def _app():
    return QApplication.instance() or QApplication([])


def test_motion_setting_labels_include_normalized_units():
    assert DISPLAY_LABELS["safe_radius"] == "Safe radius (mm)"
    assert DISPLAY_LABELS["homing_gap"] == "Homing gap (degrees)"
    assert DISPLAY_LABELS["pole_gap"] == "Pole gap (mm)"


def test_mock_dro_fields_follow_selected_grbl_streamer_type(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[scanner]\n"
        "feed_rate = 1000\n"
        "cal_tool_height = 0\n"
        "[grbl_streamer]\n"
        "type = Arduino\n"
        "baudrate = 115200\n"
        "mock_linear_speed_mm_s = 300\n"
        "mock_angular_speed_deg_s = 15\n"
        "mock_status_hz = 5\n",
        encoding="utf-8",
    )

    dialog = SettingsDialog(str(config_file), lambda: None)
    streamer_type, _kind = dialog.inputs[("grbl_streamer", "type")]
    mock_fields = [
        dialog.inputs[("grbl_streamer", key)][0]
        for key in (
            "mock_linear_speed_mm_s",
            "mock_angular_speed_deg_s",
            "mock_status_hz",
        )
    ]

    assert all(field.parentWidget().isHidden() for field in mock_fields)

    streamer_type.setCurrentText("MockSimulatedDRO")
    assert all(not field.parentWidget().isHidden() for field in mock_fields)

    streamer_type.setCurrentText("Mock")
    assert all(field.parentWidget().isHidden() for field in mock_fields)


def test_settings_dialog_saves_cylindrical_motion_manager_fields(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[motion_manager]\n"
        "type = SphericalMeasurementMotionManager\n",
        encoding="utf-8",
    )

    dialog = SettingsDialog(str(config_file), lambda: None)
    manager_type, _kind = dialog.inputs[("motion_manager", "type")]
    safe_radius, _kind = dialog.inputs[("motion_manager", "safe_radius")]

    assert isinstance(manager_type, QComboBox)
    assert isinstance(safe_radius, QLineEdit)

    manager_type.setCurrentText("CylindricalMeasurementMotionManager")
    safe_radius.setText("123.5")
    dialog.save()

    content = config_file.read_text(encoding="utf-8")
    assert "type = CylindricalMeasurementMotionManager" in content
    assert "safe_radius = 123.5" in content


def test_settings_dialog_removes_stale_motion_manager_fields(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[motion_manager]\n"
        "type = CylindricalMeasurementMotionManager\n"
        "safe_radius = 123.5\n",
        encoding="utf-8",
    )

    dialog = SettingsDialog(str(config_file), lambda: None)
    manager_type, _kind = dialog.inputs[("motion_manager", "type")]

    assert isinstance(manager_type, QComboBox)

    manager_type.setCurrentText("SphericalMeasurementMotionManager")
    dialog.save()

    content = config_file.read_text(encoding="utf-8")
    assert "type = SphericalMeasurementMotionManager" in content
    assert "safe_radius" not in content


def test_settings_dialog_saves_referenced_measurement_points_fields(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[motion_manager]\n"
        "type = CylindricalMeasurementMotionManager\n"
        "measurement_points = points\n"
        "[points]\n"
        "type = FileMeasurementPoints\n"
        "filename = old.csv\n",
        encoding="utf-8",
    )

    dialog = SettingsDialog(str(config_file), lambda: None)
    mp_type, _kind = dialog.inputs[("__motion_manager_ui__", "measurement_points_type")]
    filename, _kind = dialog.inputs[("__measurement_points__", "filename")]

    assert isinstance(mp_type, QComboBox)
    assert isinstance(filename, QLineEdit)

    mp_type.setCurrentText("FileMeasurementPoints")
    filename.setText("new.csv")
    dialog.save()

    content = config_file.read_text(encoding="utf-8")
    assert "measurement_points = points" in content
    assert "[points]" in content
    assert "type = FileMeasurementPoints" in content
    assert "filename = new.csv" in content


def test_settings_dialog_can_inline_measurement_points_fields(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[motion_manager]\n"
        "type = CylindricalMeasurementMotionManager\n"
        "measurement_points = points\n"
        "[points]\n"
        "type = FileMeasurementPoints\n"
        "filename = old.csv\n",
        encoding="utf-8",
    )

    dialog = SettingsDialog(str(config_file), lambda: None)
    section, _kind = dialog.inputs[("__motion_manager_ui__", "measurement_points_section")]
    mp_type, _kind = dialog.inputs[("__motion_manager_ui__", "measurement_points_type")]
    filename, _kind = dialog.inputs[("__measurement_points__", "filename")]

    assert isinstance(section, QLineEdit)
    assert isinstance(mp_type, QComboBox)
    assert isinstance(filename, QLineEdit)

    section.setText("")
    mp_type.setCurrentText("FileMeasurementPoints")
    filename.setText("inline.csv")
    dialog.save()

    content = config_file.read_text(encoding="utf-8")
    assert "measurement_points =" not in content
    assert "measurement_points_type = FileMeasurementPoints" in content
    assert "filename = inline.csv" in content
    assert "[points]" not in content
