from unittest.mock import Mock, call
import pytest
from nfs.datatypes import CylindricalPosition
from nfs.motion_manager import CylindricalMeasurementMotionManager


@pytest.fixture
def scanner():
    return Mock()


@pytest.fixture
def measurement_points():
    return Mock()


def test_move_to_safe_starting_radius(scanner, measurement_points):
    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points, safe_radius=300.0)
    motion_manager.move_to_safe_starting_radius()
    scanner.planar_move_to.assert_called_once_with(300.0, 0.0)


def test_next_simple_radial(scanner, measurement_points):
    # Z constant, R changes
    current_pos = CylindricalPosition(100.0, 0.0, 50.0)
    next_pos = CylindricalPosition(150.0, 0.0, 50.0)

    scanner.get_position.return_value = current_pos
    measurement_points.next.return_value = next_pos

    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points, safe_radius=200.0)
    result = motion_manager.next()

    assert result == next_pos
    # No angular move needed
    scanner.angular_move_to.assert_not_called()
    # Radial move to 150.0
    scanner.radial_move_to.assert_called_once_with(150.0)


def test_next_complex_planar_move_out(scanner, measurement_points):
    # Z changes, R changes. Current R < Safe R
    current_pos = CylindricalPosition(100.0, 0.0, 50.0)
    next_pos = CylindricalPosition(150.0, 0.0, 100.0)

    scanner.get_position.side_effect = [current_pos, current_pos, current_pos,
                                        CylindricalPosition(200.0, 0.0, 50.0),  # after radial to safe
                                        CylindricalPosition(200.0, 0.0, 100.0)]  # after vertical
    measurement_points.next.return_value = next_pos

    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points, safe_radius=200.0)
    motion_manager.next()

    # Planar move:
    # 1. Move to safe radius (200.0) because Z changes
    # 2. Move Z to 100.0
    # 3. Move R to 150.0

    scanner.radial_move_to.assert_has_calls([call(200.0), call(150.0)])
    scanner.vertical_move_to.assert_called_once_with(100.0)


def test_next_angular_move(scanner, measurement_points):
    current_pos = CylindricalPosition(100.0, 0.0, 50.0)
    next_pos = CylindricalPosition(100.0, 90.0, 50.0)  # Only angle changes

    scanner.get_position.return_value = current_pos
    measurement_points.next.return_value = next_pos

    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points, safe_radius=200.0)
    motion_manager.next()

    scanner.angular_move_to.assert_called_once_with(90.0)
    # R and Z constant
    scanner.radial_move_to.assert_not_called()
    scanner.vertical_move_to.assert_not_called()


def test_ready(scanner, measurement_points):
    measurement_points.ready.return_value = True
    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points, safe_radius=200.0)
    assert motion_manager.ready() is True


def test_reset(scanner, measurement_points):
    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points, safe_radius=200.0)
    motion_manager.reset()
    measurement_points.reset.assert_called_once()


def test_shutdown(scanner, measurement_points):
    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points, safe_radius=200.0)
    motion_manager.shutdown()
    scanner.shutdown.assert_called_once()
