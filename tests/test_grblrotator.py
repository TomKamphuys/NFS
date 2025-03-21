from unittest.mock import Mock

from grbl_controller import IGrblController
from rotator import GrblRotator


def test_move_to():
    grbl_controller_mock = Mock(spec=IGrblController)
    steps_per_degree = 10.0
    rotator = GrblRotator(grbl_controller_mock, steps_per_degree)
    angle = 30.0

    rotator.move_to(angle)

    expected_steps = int(steps_per_degree * angle)
    grbl_controller_mock.send_and_wait_for_move_ready.assert_called_once_with(f'G0 X{expected_steps}')


def test_set_as_zero():
    grbl_controller_mock = Mock(spec=IGrblController)
    rotator = GrblRotator(grbl_controller_mock, 10.0)

    rotator.set_as_zero()

    grbl_controller_mock.send.assert_any_call(GrblRotator.ZERO_POSITION_COMMAND)
    grbl_controller_mock.send.assert_any_call(GrblRotator.REPORT_POSITION_COMMAND)


def test_shutdown():
    grbl_controller_mock = Mock(spec=IGrblController)
    rotator = GrblRotator(grbl_controller_mock, 10.0)

    rotator.shutdown()

    grbl_controller_mock.shutdown.assert_called_once()


def test_move_to_negative_angle():
    grbl_controller_mock = Mock(spec=IGrblController)
    steps_per_degree = 15.0  # more fine-grained control
    rotator = GrblRotator(grbl_controller_mock, steps_per_degree)
    angle = -45.0

    rotator.move_to(angle)

    expected_steps = int(steps_per_degree * angle)
    grbl_controller_mock.send_and_wait_for_move_ready.assert_called_once_with(f'G0 X{expected_steps}')
