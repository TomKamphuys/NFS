from unittest.mock import Mock
import pytest
from nfs.datatypes import CylindricalPosition
from nfs.motion_manager import SphericalMeasurementMotionManager, SphericalNoFlyZone


@pytest.fixture
def mocks():
    return {
        'scanner': Mock(),
        'measurement_points': Mock()
    }


def test_move_to_safe_starting_position(mocks):
    # The retract radius now comes from the no-fly zone, not from a separate
    # safe_radius setting.
    no_fly_radius = 320.0
    motion_manager = SphericalMeasurementMotionManager(
        mocks['scanner'], mocks['measurement_points'],
        no_fly_zone=SphericalNoFlyZone(no_fly_radius))

    motion_manager.move_to_safe_starting_radius()

    mocks['scanner'].planar_move_to.assert_called_once_with(no_fly_radius, 0.0)


def test_next(mocks):
    current_position = CylindricalPosition(100.0, 0.0, 100.0)
    next_position = CylindricalPosition(100.0, 10.0, 110.0)

    mocks['scanner'].get_position.return_value = current_position
    mocks['measurement_points'].next.return_value = next_position

    # No-fly sphere small enough that this move stays outside it -> normal move.
    motion_manager = SphericalMeasurementMotionManager(
        mocks['scanner'], mocks['measurement_points'],
        no_fly_zone=SphericalNoFlyZone(50.0))
    motion_manager.next()

    mocks['scanner'].angular_move_to.assert_called_once_with(next_position.t())
    # Should perform planar move to next position
    mocks['scanner'].planar_move_to.assert_called()


def test_next_evasive_retracts_to_no_fly_radius(mocks):
    # Both endpoints lie inside the protected sphere -> the manager must retract
    # to the no-fly radius before proceeding.
    current_position = CylindricalPosition(10.0, 0.0, 10.0)
    next_position = CylindricalPosition(20.0, 10.0, 10.0)

    mocks['scanner'].get_position.return_value = current_position
    mocks['measurement_points'].next.return_value = next_position

    motion_manager = SphericalMeasurementMotionManager(
        mocks['scanner'], mocks['measurement_points'],
        no_fly_zone=SphericalNoFlyZone(300.0))
    motion_manager.next()

    # The first planar move retracts to the no-fly radius (length == 300.0).
    first_call = mocks['scanner'].planar_move_to.call_args_list[0]
    x_plane, z = first_call.args
    assert (x_plane ** 2 + z ** 2) ** 0.5 == pytest.approx(300.0)


def test_ready(mocks):
    mocks['measurement_points'].ready.return_value = True
    motion_manager = SphericalMeasurementMotionManager(
        mocks['scanner'], mocks['measurement_points'],
        no_fly_zone=SphericalNoFlyZone(300.0))
    assert motion_manager.ready() is True


def test_shutdown(mocks):
    motion_manager = SphericalMeasurementMotionManager(
        mocks['scanner'], mocks['measurement_points'],
        no_fly_zone=SphericalNoFlyZone(300.0))
    motion_manager.shutdown()
    mocks['scanner'].shutdown.assert_called_once()
