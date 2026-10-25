import os
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from harmonic_drive_qt.qt_compat import QApplication, QComboBox, QLineEdit, QMessageBox
from harmonic_drive_qt.settings_dialog import SettingsDialog
from harmonic_drive_qt.config_support import DISPLAY_LABELS


def _app():
    return QApplication.instance() or QApplication([])


def test_settings_dialog_fixes_motion_manager_and_measurement_points_type(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[motion_manager]\n"
        "type = FastCylindricalMeasurementMotionManager\n"
        "unsafe_point_policy = abort\n"
        "measurement_points_type = FileMeasurementPoints\n"
        "filename = test.csv\n",
        encoding="utf-8",
    )

    dialog = SettingsDialog(str(config_file), lambda changed: None)
    manager_type, _kind = dialog.inputs[("motion_manager", "type")]
    mp_type, _kind = dialog.inputs[("__motion_manager_ui__", "measurement_points_type")]

    assert isinstance(manager_type, QComboBox)
    assert not manager_type.isEnabled()
    assert manager_type.currentText() == "FastCylindricalMeasurementMotionManager"

    assert isinstance(mp_type, QComboBox)
    assert not mp_type.isEnabled()
    assert mp_type.currentText() == "FileMeasurementPoints"

    assert "no_fly_radius" not in dialog.motion_manager_extra_widgets or not dialog.motion_manager_extra_widgets["no_fly_radius"].isVisible()
    assert "no_fly_z_min" not in dialog.motion_manager_extra_widgets
    assert "no_fly_z_max" not in dialog.motion_manager_extra_widgets


def test_motion_setting_labels_include_normalized_units():
    assert DISPLAY_LABELS["homing_gap"] == "Homing gap (degrees)"
    assert DISPLAY_LABELS["pole_gap"] == "Pole gap (mm)"


def test_numeric_setting_labels_show_correct_units():
    assert DISPLAY_LABELS["feed_rate"] == "Feed rate (mm/min)"
    assert DISPLAY_LABELS["cal_tool_height"] == "Calibration tool height (mm)"
    assert DISPLAY_LABELS["cap_spacing"] == "Cap spacing (mm)"
    assert DISPLAY_LABELS["wall_spacing"] == "Wall spacing (mm)"
    assert DISPLAY_LABELS["radius"] == "Radius (mm)"
    assert DISPLAY_LABELS["height"] == "Height (mm)"
    assert DISPLAY_LABELS["speaker_height"] == "Speaker height (mm)"
    assert DISPLAY_LABELS["speaker_width"] == "Speaker width (mm)"
    assert DISPLAY_LABELS["speaker_depth"] == "Speaker depth (mm)"


def test_settings_dialog_fonts_have_point_sizes():
    _app()
    dialog = SettingsDialog("config.ini", lambda changed: None)
    dialog.ensurePolished()

    fonts = [dialog.font()]
    fonts.extend(
        widget.font()
        for widget in dialog.findChildren(object)
        if callable(getattr(widget, "font", None))
    )

    assert all(font.pointSizeF() > 0 for font in fonts)


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

    dialog = SettingsDialog(str(config_file), lambda changed: None)
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


def test_settings_dialog_hides_audio_device_selection_fields(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[audio]\n"
        "mode = hardware\n"
        "in_dev = 4\n"
        "out_dev = 4\n"
        "in_dev_name = MOTU M Series\n"
        "in_dev_hostapi = ASIO\n"
        "out_dev_name = MOTU M Series\n"
        "out_dev_hostapi = ASIO\n",
        encoding="utf-8",
    )

    dialog = SettingsDialog(str(config_file), lambda changed: None)

    assert ("audio", "mode") in dialog.inputs
    for key in (
        "in_dev",
        "out_dev",
        "in_dev_name",
        "in_dev_hostapi",
        "out_dev_name",
        "out_dev_hostapi",
    ):
        assert ("audio", key) not in dialog.inputs


def test_settings_dialog_restore_defaults_uses_config_default_ini(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    default_file = tmp_path / "config_default.ini"
    config_file.write_text("[logging]\nlevel = ERROR\n", encoding="utf-8")
    default_file.write_text("[logging]\nlevel = INFO\n", encoding="utf-8")
    applied = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    dialog = SettingsDialog(str(config_file), lambda changed: applied.append(True))
    dialog.restore_defaults()

    assert config_file.read_text(encoding="utf-8") == default_file.read_text(encoding="utf-8")
    assert (tmp_path / "config.old").read_text(encoding="utf-8") == "[logging]\nlevel = ERROR\n"
    assert applied == [True]
    level, _kind = dialog.inputs[("logging", "level")]
    assert isinstance(level, QComboBox)
    assert level.currentData() == "INFO"


def test_settings_dialog_restore_defaults_warns_when_default_missing(tmp_path, monkeypatch):
    _app()
    monkeypatch.chdir(tmp_path)
    config_file = tmp_path / "config.ini"
    config_file.write_text("[logging]\nlevel = ERROR\n", encoding="utf-8")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    dialog = SettingsDialog(str(config_file), lambda changed: None)
    dialog.restore_defaults()

    assert "level = ERROR" in config_file.read_text(encoding="utf-8")
    assert warnings


def test_settings_dialog_saves_fast_cylindrical_motion_manager_fields(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[motion_manager]\n"
        "type = FastCylindricalMeasurementMotionManager\n"
        "unsafe_point_policy = abort\n"
        "optimize_point_order = False\n",
        encoding="utf-8",
    )

    dialog = SettingsDialog(str(config_file), lambda changed: None)
    manager_type, _kind = dialog.inputs[("motion_manager", "type")]
    policy, _kind = dialog.inputs[("motion_manager", "unsafe_point_policy")]

    assert isinstance(manager_type, QComboBox)
    assert isinstance(policy, QComboBox)

    policy.setCurrentText("skip")
    dialog.save()

    content = config_file.read_text(encoding="utf-8")
    assert "type = FastCylindricalMeasurementMotionManager" in content
    assert "unsafe_point_policy = skip" in content


def test_settings_dialog_saves_referenced_measurement_points_fields(tmp_path):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[motion_manager]\n"
        "type = FastCylindricalMeasurementMotionManager\n"
        "measurement_points = points\n"
        "[points]\n"
        "type = FileMeasurementPoints\n"
        "filename = old.csv\n",
        encoding="utf-8",
    )

    dialog = SettingsDialog(str(config_file), lambda changed: None)
    mp_type, _kind = dialog.inputs[("__motion_manager_ui__", "measurement_points_type")]
    filename, _kind = dialog.inputs[("__measurement_points__", "filename")]

    assert isinstance(mp_type, QComboBox)
    assert isinstance(filename, QLineEdit)

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

    dialog = SettingsDialog(str(config_file), lambda changed: None)
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
