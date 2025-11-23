from unittest.mock import Mock, patch

import pytest
from src.nfs.grbl_controller import Arduino, GrblStreamerMock


@pytest.fixture
def mock_config_parser():
    config_parser = Mock()
    config_parser.getboolean.return_value = True
    config_parser.getint.return_value = 115200
    config_parser.getfloat.side_effect = lambda section, setting: 100.0
    config_parser.get.side_effect = lambda section, setting: "COM3" if setting == "port" else None
    return config_parser


@pytest.fixture
def arduino_instance(mock_config_parser):
    with patch("grbl_controller.configparser.ConfigParser") as MockConfigParser:
        MockConfigParser.return_value = mock_config_parser
        return Arduino(config_file="mock_config.ini")


def test_arduino_initialization(mock_config_parser):
    with patch("grbl_controller.configparser.ConfigParser") as MockConfigParser:
        MockConfigParser.return_value = mock_config_parser
        arduino = Arduino(config_file="mock_config.ini")
        assert isinstance(arduino._grbl_streamer, GrblStreamerMock)


def test_send_message(arduino_instance):
    with patch.object(GrblStreamerMock, "send_immediately") as mock_send:
        arduino_instance.send("Test message")
        mock_send.assert_called_once_with("Test message")


def test_shutdown(arduino_instance):
    with patch.object(GrblStreamerMock, "disconnect") as mock_disconnect:
        arduino_instance.shutdown()
        mock_disconnect.assert_called_once()


# def test_send_and_wait_for_move_ready(arduino_instance):
#     with patch.object(GrblStreamerMock, "send_immediately") as mock_send:
#         with patch("time.sleep") as mock_sleep:
#             arduino_instance.send_and_wait_for_move_ready("G01 X10 Y10")
#             mock_send.assert_any_call("G01 X10 Y10")
#             mock_send.assert_any_call("G04 P0")
#             assert mock_sleep.called


def test_set_axis_according_to_config(arduino_instance, mock_config_parser):
    with patch.object(Arduino, "send") as mock_send:
        arduino_instance._set_axis_according_to_config(mock_config_parser, 'x')
        mock_send.assert_any_call("$100=100.0")
        mock_send.assert_any_call("$110=100.0")
        mock_send.assert_any_call("$120=100.0")
