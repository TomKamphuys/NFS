from unittest.mock import Mock

from src.nfs.datatypes import CylindricalPosition
from src.nfs.scanner import SphericalMeasurementMotionManager


def test_move_to_safe_starting_position():
    scanner = Mock()
    measurement_points = Mock()
    safe_radius = 320.0
    measurement_points.get_radius.return_value = safe_radius

    motion_manager = SphericalMeasurementMotionManager(scanner, measurement_points)
    motion_manager.move_to_safe_starting_radius()

    scanner.planar_move_to.assert_called_once_with(safe_radius, 0.0)

def test_next():
    scanner = Mock()
    measurement_points = Mock()
    current_position = CylindricalPosition(0.0, 1.0, 2.0)
    next_position = CylindricalPosition(1.0, 2.0, 3.0)
    scanner.get_position.return_value = current_position
    measurement_points.next.return_value = next_position

    motion_manager = SphericalMeasurementMotionManager(scanner, measurement_points)
    motion_manager.next()

    scanner.angular_move_to.assert_called_once_with(next_position.t())
    scanner.planar_move_to.assert_called()
    scanner.ccw_arc_move_to.assert_called()
    # TODO more cases will have to be tested


def test_ready():
    scanner = Mock()
    measurement_points = Mock()
    ready_value = True
    measurement_points.ready.return_value = ready_value

    motion_manager = SphericalMeasurementMotionManager(scanner, measurement_points)
    result = motion_manager.ready()

    assert result == ready_value


def test_shutdown():
    scanner = Mock()
    measurement_points = Mock()

    motion_manager = SphericalMeasurementMotionManager(scanner, measurement_points)
    motion_manager.shutdown()

    scanner.shutdown.assert_called_once_with()
