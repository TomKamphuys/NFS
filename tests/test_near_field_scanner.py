from unittest.mock import Mock

from src.nfs.datatypes import CylindricalPosition
from src.nfs.nfs import NearFieldScanner


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

    assert measurement_motion_manager.move_to_safe_starting_radius.call_count == 2
    measurement_motion_manager.ready.assert_called()
    measurement_motion_manager.reset.assert_called_once_with()
    scanner.angular_move_to.assert_called_once_with(0.0)


def test_shutdown():
    scanner = Mock()
    audio = Mock()
    measurement_motion_manager = Mock()

    nfs = NearFieldScanner(scanner, audio, measurement_motion_manager)
    nfs.shutdown()

    scanner.shutdown.assert_called_once_with()
