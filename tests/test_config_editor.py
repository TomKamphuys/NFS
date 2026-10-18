import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harmonic_drive_qt.config_support import (
    _coerce, _format_for_ini, _strip_inline_comment, _parse_bool,
    _serial_port_options, validate_dut_volume,
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
    assert _coerce("int", "42,5") == 42
    assert _coerce("float", "3.14") == 3.14
    assert _coerce("float", "3,14") == 3.14
    assert _coerce("opt_float", "None") is None
    assert _coerce("opt_float", "") is None
    assert _coerce("opt_float", "1.2") == 1.2
    assert _coerce("opt_float", "1,2") == 1.2
    assert _coerce("optional_float", "None") is None
    assert _coerce("optional_float", "") is None
    assert _coerce("optional_float", "1.2") == 1.2
    assert _coerce("optional_float", "1,2") == 1.2

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


# --- Unit tests for validate_dut_volume ---

def _write_config(content: str) -> str:
    """Write *content* to a temporary config file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".ini")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def test_validate_dut_volume_complete():
    cfg = _write_config(
        "[dut_volume]\n"
        "top_r = 290.0\ntop_phi = 45.0\ntop_z = 350.0\n"
        "bot_r = 39.0\nbot_phi = 0.0\nbot_z = -50.0\n"
    )
    try:
        assert validate_dut_volume(cfg) == []
    finally:
        os.unlink(cfg)


def test_validate_dut_volume_missing_section():
    cfg = _write_config("[motion_manager]\ntype = mock\n")
    try:
        missing = validate_dut_volume(cfg)
        assert len(missing) == 6
    finally:
        os.unlink(cfg)


def test_validate_dut_volume_empty_fields():
    cfg = _write_config(
        "[dut_volume]\n"
        "top_r = 290.0\ntop_phi = 45.0\ntop_z = 350.0\n"
        "bot_r = \nbot_phi = \nbot_z = \n"
    )
    try:
        missing = validate_dut_volume(cfg)
        assert "Bot Radius (mm)" in missing
        assert "Bot Phi (deg)" in missing
        assert "Bot Height (mm)" in missing
        assert len(missing) == 3
    finally:
        os.unlink(cfg)


def test_validate_dut_volume_non_numeric():
    cfg = _write_config(
        "[dut_volume]\n"
        "top_r = abc\ntop_phi = 45.0\ntop_z = 350.0\n"
        "bot_r = 39.0\nbot_phi = 0.0\nbot_z = -50.0\n"
    )
    try:
        missing = validate_dut_volume(cfg)
        assert "Top Radius (mm)" in missing
        assert len(missing) == 1
    finally:
        os.unlink(cfg)


def test_validate_dut_volume_none_value():
    cfg = _write_config(
        "[dut_volume]\n"
        "top_r = None\ntop_phi = 45.0\ntop_z = 350.0\n"
        "bot_r = 39.0\nbot_phi = 0.0\nbot_z = -50.0\n"
    )
    try:
        missing = validate_dut_volume(cfg)
        assert "Top Radius (mm)" in missing
    finally:
        os.unlink(cfg)


def test_validate_dut_volume_nonexistent_file():
    missing = validate_dut_volume("/nonexistent/path/config.ini")
    assert len(missing) == 6


def test_validate_dut_volume_negative_top_radius():
    cfg = _write_config(
        "[dut_volume]\n"
        "top_r = -10.0\ntop_phi = 45.0\ntop_z = 350.0\n"
        "bot_r = 39.0\nbot_phi = 0.0\nbot_z = -50.0\n"
    )
    try:
        missing = validate_dut_volume(cfg)
        assert "Top Radius (mm)" in missing
        assert len(missing) == 1
    finally:
        os.unlink(cfg)


def test_validate_dut_volume_negative_bot_radius():
    cfg = _write_config(
        "[dut_volume]\n"
        "top_r = 290.0\ntop_phi = 45.0\ntop_z = 350.0\n"
        "bot_r = -5.0\nbot_phi = 0.0\nbot_z = -50.0\n"
    )
    try:
        missing = validate_dut_volume(cfg)
        assert "Bot Radius (mm)" in missing
        assert len(missing) == 1
    finally:
        os.unlink(cfg)


def test_validate_dut_volume_negative_height_allowed():
    cfg = _write_config(
        "[dut_volume]\n"
        "top_r = 290.0\ntop_phi = 45.0\ntop_z = 350.0\n"
        "bot_r = 39.0\nbot_phi = 0.0\nbot_z = -50.0\n"
    )
    try:
        # Negative height values are valid (e.g., below the reference plane)
        assert validate_dut_volume(cfg) == []
    finally:
        os.unlink(cfg)
