from unittest.mock import Mock
from datatypes import CylindricalPosition
from nfs import NearFieldScanner


def test_take_single_measurement():
    scanner = Mock()
    audio = Mock()
    measurement_motion_manager = Mock()
    position = CylindricalPosition(1.0, 2.0, 3.0)
    scanner.get_position.return_value = position

    nfs = NearFieldScanner(scanner, audio, measurement_motion_manager)

    nfs.take_single_measurement()

    scanner.get_position.assert_called_once_with()
    audio.measure_ir.assert_called_once_with(position)


def test_take_measurement_set():
    scanner = Mock()
    audio = Mock()
    measurement_motion_manager = Mock()
    measurement_motion_manager.ready.return_value = True

    nfs = NearFieldScanner(scanner, audio, measurement_motion_manager)
    nfs.take_measurement_set()

    measurement_motion_manager.move_to_safe_starting_position.assert_called_once_with()
    measurement_motion_manager.ready.assert_called_once_with()
    scanner.shutdown.assert_called_once_with()

    # TODO de while loop nog testen


def test_shutdown():
    scanner = Mock()
    audio = Mock()
    measurement_motion_manager = Mock()

    nfs = NearFieldScanner(scanner, audio, measurement_motion_manager)
    nfs.shutdown()

    scanner.shutdown.assert_called_once_with()
