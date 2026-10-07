from unittest.mock import Mock
import pytest
from nfs.datatypes import CylindricalPosition
from nfs.motion_manager import (
    SphericalMeasurementMotionManager,
    SphericalNoFlyZone,
    UnsafeMeasurementPointError,
    UNSAFE_POINT_SKIP,
)


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
    # Arm starts inside the no-fly zone, so it must move out to the safe radius.
    mocks['scanner'].get_position.return_value = CylindricalPosition(0.0, 0.0, 0.0)
    motion_manager = SphericalMeasurementMotionManager(
        mocks['scanner'], mocks['measurement_points'],
        no_fly_zone=SphericalNoFlyZone(no_fly_radius))

    motion_manager.move_to_safe_starting_radius()

    mocks['scanner'].planar_move_to.assert_called_once_with(no_fly_radius, 0.0)


def test_move_to_safe_starting_position_skipped_when_already_outside(mocks):
    # Arm already rests outside the no-fly zone, so no initial radial move is needed.
    no_fly_radius = 320.0
    mocks['scanner'].get_position.return_value = CylindricalPosition(400.0, 0.0, 0.0)
    motion_manager = SphericalMeasurementMotionManager(
        mocks['scanner'], mocks['measurement_points'],
        no_fly_zone=SphericalNoFlyZone(no_fly_radius))

    motion_manager.move_to_safe_starting_radius()

    mocks['scanner'].planar_move_to.assert_not_called()


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


def test_next_aborts_when_point_inside_zone(mocks):
    # A target that lies inside the protected sphere must never be driven to;
    # with the default 'abort' policy the manager raises before any motion.
    current_position = CylindricalPosition(400.0, 0.0, 0.0)
    unsafe_position = CylindricalPosition(20.0, 10.0, 10.0)  # length ~24 < 300

    mocks['scanner'].get_position.return_value = current_position
    mocks['measurement_points'].next.return_value = unsafe_position

    motion_manager = SphericalMeasurementMotionManager(
        mocks['scanner'], mocks['measurement_points'],
        no_fly_zone=SphericalNoFlyZone(300.0))

    with pytest.raises(UnsafeMeasurementPointError):
        motion_manager.next()

    # No motion command was issued for the unsafe point.
    mocks['scanner'].planar_move_to.assert_not_called()
    mocks['scanner'].angular_move_to.assert_not_called()


def test_next_skips_point_inside_zone(mocks):
    # With the 'skip' policy an interior point is skipped: the next safe point is
    # used instead and no move is made towards the unsafe one.
    current_position = CylindricalPosition(400.0, 0.0, 0.0)
    unsafe_position = CylindricalPosition(20.0, 10.0, 10.0)   # inside the sphere
    safe_position = CylindricalPosition(400.0, 10.0, 10.0)    # outside the sphere

    mocks['scanner'].get_position.return_value = current_position
    mocks['measurement_points'].next.side_effect = [unsafe_position, safe_position]
    mocks['measurement_points'].ready.return_value = False

    motion_manager = SphericalMeasurementMotionManager(
        mocks['scanner'], mocks['measurement_points'],
        no_fly_zone=SphericalNoFlyZone(300.0),
        unsafe_point_policy=UNSAFE_POINT_SKIP)

    result = motion_manager.next()

    assert result == safe_position
    # Two points were consumed: the unsafe one (skipped) and the safe one.
    assert mocks['measurement_points'].next.call_count == 2


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
