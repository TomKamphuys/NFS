from unittest.mock import Mock
import pytest
from nfs.datatypes import CylindricalPosition
from nfs.motion_manager import SphericalMeasurementMotionManager


@pytest.fixture
def mocks():
    return {
        'scanner': Mock(),
        'measurement_points': Mock()
    }


def test_move_to_safe_starting_position(mocks):
    # SphericalMeasurementMotionManager takes (scanner, measurement_points)
    motion_manager = SphericalMeasurementMotionManager(mocks['scanner'], mocks['measurement_points'])
    
    # It gets safe_radius from measurement_points.get_radius()
    safe_radius = 320.0
    mocks['measurement_points'].get_radius.return_value = safe_radius
    
    motion_manager.move_to_safe_starting_radius()

    mocks['scanner'].planar_move_to.assert_called_once_with(safe_radius, 0.0)


def test_next(mocks):
    current_position = CylindricalPosition(100.0, 0.0, 100.0)
    next_position = CylindricalPosition(100.0, 10.0, 110.0)
    
    mocks['scanner'].get_position.return_value = current_position
    mocks['measurement_points'].next.return_value = next_position
    mocks['measurement_points'].need_to_do_evasive_move.return_value = False

    motion_manager = SphericalMeasurementMotionManager(mocks['scanner'], mocks['measurement_points'])
    motion_manager.next()

    mocks['scanner'].angular_move_to.assert_called_once_with(next_position.t())
    # Should perform planar move to next position
    mocks['scanner'].planar_move_to.assert_called()


def test_ready(mocks):
    mocks['measurement_points'].ready.return_value = True
    motion_manager = SphericalMeasurementMotionManager(mocks['scanner'], mocks['measurement_points'])
    assert motion_manager.ready() is True


def test_shutdown(mocks):
    motion_manager = SphericalMeasurementMotionManager(mocks['scanner'], mocks['measurement_points'])
    motion_manager.shutdown()
    mocks['scanner'].shutdown.assert_called_once()
