from unittest.mock import Mock

from src.nfs.grbl_controller import IGrblController
from src.nfs.rotator import GrblRotator


def test_move_to():
    grbl_controller_mock = Mock(spec=IGrblController)
    axis = 'Z'
    rotator = GrblRotator(grbl_controller_mock, axis)
    angle = 30.0

    rotator.move_to(angle)

    grbl_controller_mock.send_and_wait_for_move_ready.assert_called_once_with(f'G0 {axis}{angle:.1f}')


def test_set_as_zero():
    grbl_controller_mock = Mock(spec=IGrblController)
    axis = 'Z'
    rotator = GrblRotator(grbl_controller_mock, axis)

    rotator.set_as_zero()

    grbl_controller_mock.send.assert_any_call(GrblRotator.ZERO_POSITION_COMMAND)
    grbl_controller_mock.send.assert_any_call(GrblRotator.REPORT_POSITION_COMMAND)


def test_shutdown():
    grbl_controller_mock = Mock(spec=IGrblController)
    axis = 'Z'
    rotator = GrblRotator(grbl_controller_mock, axis)

    rotator.shutdown()

    grbl_controller_mock.shutdown.assert_called_once()


def test_move_to_negative_angle():
    grbl_controller_mock = Mock(spec=IGrblController)
    axis = 'Z'
    rotator = GrblRotator(grbl_controller_mock, axis)
    angle = -45.0

    rotator.move_to(angle)

    grbl_controller_mock.send_and_wait_for_move_ready.assert_called_once_with(f'G0 {axis}{angle:.1f}')
