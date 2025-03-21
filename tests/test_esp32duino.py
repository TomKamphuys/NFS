from unittest.mock import Mock, call

from loguru import logger

from grbl_controller import ESP32Duino

# logger.remove(0)
logger.add('ESP32Duino.log', mode='w', level="TRACE", backtrace=True, diagnose=True)


def test_initialization():
    mock_fluidnc = Mock()
    mock_fluidnc.recv.return_value = b'ok\n'

    esp32duino = ESP32Duino(mock_fluidnc)

    mock_fluidnc.send.assert_called_once_with('$X\n')


def test_shutdown():
    mock_fluidnc = Mock()
    mock_fluidnc.recv.return_value = b'ok\n'

    esp32duino = ESP32Duino(mock_fluidnc)

    esp32duino.shutdown()

    mock_fluidnc.close.assert_called_once()


def test_send():
    mock_fluidnc = Mock()
    mock_fluidnc.recv.return_value = b'ok\n'

    mock_message = "test_message"

    esp32duino = ESP32Duino(mock_fluidnc)

    esp32duino.send(mock_message)

    mock_fluidnc.send.assert_called_with(f'{mock_message}\n')


def test_send_and_wait_for_move_ready():
    mock_fluidnc = Mock()
    mock_fluidnc.recv.side_effect = [b'ok\n', b'ok\n', b'Idle\n']
    mock_message = "test_message"

    esp32duino = ESP32Duino(mock_fluidnc)

    esp32duino.send_and_wait_for_move_ready(mock_message)

    calls = [call('$X\n'), call(f'{mock_message}\n'), call('?')]
    mock_fluidnc.send.assert_has_calls(calls)
