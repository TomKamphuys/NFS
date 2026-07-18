from unittest.mock import MagicMock, patch

import pytest

from harmonic_drive_qt.config_support import (
    _coerce, _format_for_ini, _strip_inline_comment, _parse_bool,
    _serial_port_options,
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
