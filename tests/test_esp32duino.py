from unittest.mock import Mock
import pytest
from grbl_controller import ESP32Duino


def test_initialization():
    mock_fluidnc = Mock()
    mock_fluidnc.recv.return_value = b'ok\n'

    esp32duino = ESP32Duino(mock_fluidnc)

    mock_fluidnc.send.assert_called_with('$X\n')
    assert mock_fluidnc.recv.call_count == 3


def test_shutdown():
    mock_fluidnc = Mock()
    esp32duino = ESP32Duino(mock_fluidnc)

    esp32duino.shutdown()

    mock_fluidnc.close.assert_called_once()


def test_send():
    mock_fluidnc = Mock()
    mock_message = "test_message"
    esp32duino = ESP32Duino(mock_fluidnc)

    esp32duino.send(mock_message)

    mock_fluidnc.send.assert_called_with(f'{mock_message}\n')

def test_send_and_wait():
    mock_fluidnc = Mock()
    mock_fluidnc.recv.side_effect = [b'ok\n', b'ok\n', b'ok\n', b'ok\n', b'ok\n']
    esp32duino = ESP32Duino(mock_fluidnc)

    esp32duino.send_and_wait("test_message")

    assert mock_fluidnc.send.call_count == 3  # send message, send G04P0, and $X
    assert mock_fluidnc.recv.call_count == 5  # two '_wait_for_ok' calls, 3 calls at init two clear buffer

def test_receive():
    mock_fluidnc = Mock()
    mock_fluidnc.recv.side_effect = [b'ok\n', b'ok\n', b'ok\n', b'ok\n']

    esp32duino = ESP32Duino(mock_fluidnc)
    result = esp32duino._receive()

    assert result == "ok"
    assert mock_fluidnc.recv.call_count == 4