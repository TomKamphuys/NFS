import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from harmonic_drive_qt.qt_compat import QApplication, QComboBox, QLineEdit
from harmonic_drive_qt.settings_dialog import SettingsDialog


def _app():
    return QApplication.instance() or QApplication([])


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
