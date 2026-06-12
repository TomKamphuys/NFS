import configparser
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch
from harmonic_drive.config_editor import (
    _coerce, _format_for_ini, _on_ok, _strip_inline_comment, _parse_bool,
    _serial_port_options, open_config_editor, restore_default_config,
    set_file_measurement_points_filename
)

# --- Unit tests for helper functions ---

def test_strip_inline_comment():
    assert _strip_inline_comment("value # comment") == "value"
    assert _strip_inline_comment("value ; comment") == "value"
    assert _strip_inline_comment("  value  ") == "value"
    assert _strip_inline_comment(None) == ""
    assert _strip_inline_comment("no comment") == "no comment"

def test_parse_bool():
    assert _parse_bool("1") is True
    assert _parse_bool("true") is True
    assert _parse_bool("YES") is True
    assert _parse_bool("on") is True
    assert _parse_bool("0") is False
    assert _parse_bool("false") is False
    assert _parse_bool("no") is False
    assert _parse_bool("off") is False
    assert _parse_bool("anything") is False

def test_coerce():
    assert _coerce("str", " hello ") == "hello"
    assert _coerce("choice", "option1") == "option1"
    assert _coerce("bool", "true") is True
    assert _coerce("int", "42.5") == 42
    assert _coerce("float", "3.14") == 3.14
    assert _coerce("opt_float", "None") is None
    assert _coerce("opt_float", "") is None
    assert _coerce("opt_float", "1.2") == 1.2
    assert _coerce("optional_float", "None") is None
    assert _coerce("optional_float", "") is None
    assert _coerce("optional_float", "1.2") == 1.2
    
    with pytest.raises(ValueError, match="Unknown kind"):
        _coerce("unknown", "val")
    
    with pytest.raises(ValueError):
        _coerce("int", "not a number")

def test_format_for_ini():
    assert _format_for_ini("bool", True) == "True"
    assert _format_for_ini("bool", False) == "False"
    assert _format_for_ini("opt_float", None) == "None"
    assert _format_for_ini("optional_float", None) == "None"
    assert _format_for_ini("float", 1.2) == "1.2"
    assert _format_for_ini("str", "val") == "val"


def test_serial_port_options_lists_detected_ports():
    port_a = MagicMock()
    port_a.device = "COM7"
    port_a.description = "Arduino Uno"
    port_b = MagicMock()
    port_b.device = "COM3"
    port_b.description = ""

    with patch("serial.tools.list_ports.comports", return_value=[port_a, port_b]):
        options = _serial_port_options("COM7")

    assert list(options) == ["COM3", "COM7"]
    assert options["COM3"] == "COM3"
    assert options["COM7"] == "COM7: Arduino Uno"


def test_serial_port_options_preserves_configured_missing_port():
    port = MagicMock()
    port.device = "COM7"
    port.description = "Arduino Uno"

    with patch("serial.tools.list_ports.comports", return_value=[port]):
        options = _serial_port_options("COM5")

    assert list(options) == ["COM5", "COM7"]
    assert options["COM5"] == "COM5 (configured, not currently detected)"

# --- Tests for _on_ok logic (Saving and Backups) ---

def test_on_ok_success(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text("[nfs]\naudio = audio\n[audio]\nmode = mock\n")
    
    parser = configparser.ConfigParser()
    parser.read(config_file)
    
    # Mock UI elements
    mock_el = MagicMock()
    mock_el.value = "hardware"
    
    static_inputs = {("audio", "mode"): mock_el}
    dyn = {"mm_type_select": None}
    dialog = MagicMock()
    on_apply = MagicMock()
    
    with patch("harmonic_drive.config_editor.ui.notify") as mock_notify:
        _on_ok(parser, config_file, static_inputs, dyn, dialog, on_apply)
        
        # Check backup
        backup_file = config_file.with_suffix(".old")
        assert backup_file.exists()
        assert "mode = mock" in backup_file.read_text()
        
        # Check saved file
        assert "mode = hardware" in config_file.read_text()
        
        dialog.close.assert_called_once()
        on_apply.assert_called_once()
        mock_notify.assert_not_called()

def test_on_ok_validation_error(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text("[audio]\nin_dev = 1\n")
    
    parser = configparser.ConfigParser()
    parser.read(config_file)
    
    mock_el = MagicMock()
    mock_el.value = "invalid_int"
    
    static_inputs = {("audio", "in_dev"): mock_el}
    dyn = {"mm_type_select": None}
    dialog = MagicMock()
    on_apply = MagicMock()
    
    with patch("harmonic_drive.config_editor.ui.notify") as mock_notify:
        _on_ok(parser, config_file, static_inputs, dyn, dialog, on_apply)
        
        mock_notify.assert_called_once()
        assert "invalid value" in mock_notify.call_args[0][0]
        dialog.close.assert_not_called()
        on_apply.assert_not_called()


def test_on_ok_scanner_grbl_connection_fields(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[scanner]\n"
        "feed_rate = 35000\n"
        "cal_tool_height = 0.0\n"
        "\n"
        "[grbl_streamer]\n"
        "type = Mock\n"
        "baudrate = 115200\n"
        "\n"
        "[windows]\n"
        "port = COM5\n"
    )

    parser = configparser.ConfigParser()
    parser.read(config_file)

    grbl_type = MagicMock()
    grbl_type.value = "Arduino"
    com_port = MagicMock()
    com_port.value = "COM8"
    baudrate = MagicMock()
    baudrate.value = "250000"
    cal_tool_height = MagicMock()
    cal_tool_height.value = "30.0"

    static_inputs = {
        ("scanner", "cal_tool_height"): cal_tool_height,
        ("grbl_streamer", "type"): grbl_type,
        ("grbl_streamer", "baudrate"): baudrate,
        ("windows", "port"): com_port,
    }
    dyn = {"mm_type_select": None}
    dialog = MagicMock()
    on_apply = MagicMock()

    _on_ok(parser, config_file, static_inputs, dyn, dialog, on_apply)

    saved = configparser.ConfigParser()
    saved.read(config_file)

    assert saved.get("grbl_streamer", "type") == "Arduino"
    assert saved.get("grbl_streamer", "baudrate") == "250000"
    assert saved.get("windows", "port") == "COM8"
    assert saved.get("scanner", "cal_tool_height") == "30.0"
    assert not saved.has_option("scanner", "type")
    assert not saved.has_option("scanner", "port")
    dialog.close.assert_called_once()
    on_apply.assert_called_once()


def test_on_ok_motion_manager_dynamic(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text("[motion_manager]\ntype = CylindricalMeasurementMotionManager\nmeasurement_points = points\n[points]\ntype = CylindricalMeasurementPoints\n")
    
    parser = configparser.ConfigParser()
    parser.read(config_file)
    
    # Mock UI elements for motion_manager
    mm_type_sel = MagicMock()
    mm_type_sel.value = "SphericalMeasurementMotionManager"
    
    mp_section_input = MagicMock()
    mp_section_input.value = "new_points"
    
    mp_type_sel = MagicMock()
    mp_type_sel.value = "SphericalMeasurementPoints"
    
    dyn = {
        "mm_type_select": mm_type_sel,
        "mp_section_input": mp_section_input,
        "mp_type_select": mp_type_sel,
        "mm_inputs": {},
        "mp_inputs": {},
        "mp_section_name": "points"
    }
    
    static_inputs = {}
    dialog = MagicMock()
    on_apply = MagicMock()
    
    _on_ok(parser, config_file, static_inputs, dyn, dialog, on_apply)
    
    content = config_file.read_text()
    assert "type = SphericalMeasurementMotionManager" in content
    assert "measurement_points = new_points" in content
    assert "[new_points]" in content
    assert "type = SphericalMeasurementPoints" in content
    # Ensure old section is removed
    assert "[points]" not in content

def test_on_ok_motion_manager_inline(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text("[motion_manager]\ntype = CylindricalMeasurementMotionManager\nmeasurement_points = points\n[points]\ntype = FileMeasurementPoints\nfilename = old.csv\n")
    
    parser = configparser.ConfigParser()
    parser.read(config_file)
    
    # Mock UI elements for motion_manager (Cylindrical)
    mm_type_sel = MagicMock()
    mm_type_sel.value = "CylindricalMeasurementMotionManager"
    
    # Empty section input -> INLINE
    mp_section_input = MagicMock()
    mp_section_input.value = ""
    
    mp_type_sel = MagicMock()
    mp_type_sel.value = "FileMeasurementPoints"
    
    # Mock some MP fields
    filename_el = MagicMock()
    filename_el.value = "new.csv"
    
    dyn = {
        "mm_type_select": mm_type_sel,
        "mp_section_input": mp_section_input,
        "mp_type_select": mp_type_sel,
        "mm_inputs": {},
        "mp_inputs": {"filename": (filename_el, "str")},
        "mp_section_name": "points"
    }
    
    static_inputs = {}
    dialog = MagicMock()
    on_apply = MagicMock()
    
    _on_ok(parser, config_file, static_inputs, dyn, dialog, on_apply)
    
    content = config_file.read_text()
    assert "[motion_manager]" in content
    assert "type = CylindricalMeasurementMotionManager" in content
    assert "measurement_points_type = FileMeasurementPoints" in content
    assert "filename = new.csv" in content
    # Ensure reference is removed
    assert "measurement_points =" not in content
    # Ensure old section is removed
    assert "[points]" not in content


def test_on_ok_optional_float_blank_removes_key(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[motion_manager]\n"
        "type = CylindricalMeasurementMotionManager\n"
        "safe_radius = 300\n"
    )

    parser = configparser.ConfigParser()
    parser.read(config_file)

    mm_type_sel = MagicMock()
    mm_type_sel.value = "CylindricalMeasurementMotionManager"

    safe_radius_el = MagicMock()
    safe_radius_el.value = ""

    dyn = {
        "mm_type_select": mm_type_sel,
        "mp_section_input": None,
        "mp_type_select": None,
        "mm_inputs": {"safe_radius": (safe_radius_el, "optional_float")},
        "mp_inputs": {},
        "mp_section_name": None,
    }

    dialog = MagicMock()
    on_apply = MagicMock()

    _on_ok(parser, config_file, {}, dyn, dialog, on_apply)

    content = config_file.read_text()
    assert "safe_radius" not in content
    dialog.close.assert_called_once()
    on_apply.assert_called_once()


def test_set_file_measurement_points_filename_inline(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[motion_manager]\n"
        "type = CylindricalMeasurementMotionManager\n"
        "measurement_points_type = FileMeasurementPoints\n"
        "filename = old.csv\n"
    )
    on_apply = MagicMock()

    section = set_file_measurement_points_filename(
        str(config_file),
        "grid1.csv",
        on_apply,
    )

    content = config_file.read_text()
    assert section == "motion_manager"
    assert "measurement_points_type = FileMeasurementPoints" in content
    assert "filename = grid1.csv" in content
    assert config_file.with_suffix(".old").exists()
    on_apply.assert_called_once()


def test_set_file_measurement_points_filename_referenced_section(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[motion_manager]\n"
        "type = CylindricalMeasurementMotionManager\n"
        "measurement_points = grid_points\n"
        "\n"
        "[grid_points]\n"
        "type = CylindricalMeasurementPoints\n"
        "radius = 200\n"
    )

    section = set_file_measurement_points_filename(str(config_file), "grid1.csv")

    content = config_file.read_text()
    assert section == "grid_points"
    assert "measurement_points = grid_points" in content
    assert "[grid_points]" in content
    assert "type = FileMeasurementPoints" in content
    assert "filename = grid1.csv" in content


def test_restore_default_config(tmp_path):
    config_file = tmp_path / "config.ini"
    default_file = tmp_path / "default_config.ini"
    config_file.write_text("[audio]\nmode = hardware\n")
    default_file.write_text("[audio]\nmode = mock\n")
    on_apply = MagicMock()

    with patch("harmonic_drive.config_editor.ui.notify") as mock_notify:
        assert restore_default_config(config_file, on_apply) is True

    assert config_file.read_text() == "[audio]\nmode = mock\n"
    assert config_file.with_suffix(".old").exists()
    on_apply.assert_called_once()
    mock_notify.assert_called_once_with("Default configuration restored", type="positive")

# --- Test for open_config_editor (structure check) ---

@patch("harmonic_drive.config_editor.ui.dialog")
@patch("harmonic_drive.config_editor.ui.notify")
def test_open_config_editor_not_found(mock_notify, mock_dialog):
    open_config_editor("non_existent.ini", lambda: None)
    mock_notify.assert_called_once_with("Config file not found: non_existent.ini", type="negative")

@patch("harmonic_drive.config_editor.ui.dialog")
@patch("harmonic_drive.config_editor.ui.card")
@patch("harmonic_drive.config_editor.ui.tabs")
@patch("harmonic_drive.config_editor.ui.tab_panels")
@patch("harmonic_drive.config_editor.ui.button")
def test_open_config_editor_basic(mock_button, mock_panels, mock_tabs, mock_card, mock_dialog, tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text("[audio]\nmode = mock\n")
    
    # Just check if it runs without crashing and creates expected UI components
    # (Checking exact UI tree is hard without integration testing)
    open_config_editor(str(config_file), lambda: None)
    
    mock_dialog.assert_called_once()
    mock_card.assert_called_once()

# --- Test for Sound Device Window ---

@patch("harmonic_drive.config_editor.get_devices_and_channels")
@patch("harmonic_drive.config_editor.ui.dialog")
def test_show_sound_devices(mock_dialog, mock_get_devices):
    mock_get_devices.return_value = {
        0: {"name": "Test Device", "hostapi": "ASIO", "input_channels": [0], "output_channels": [0]}
    }
    from harmonic_drive.config_editor import _show_sound_devices
    _show_sound_devices()
    mock_get_devices.assert_called_once()
    mock_dialog.assert_called_once()
