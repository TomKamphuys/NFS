from unittest.mock import Mock
from datatypes import CylindricalPosition
from scanner import SphericalMeasurementMotionManager


def test_radial_move_to():
    mock_scanner = Mock()
    mock_measurement_points = Mock()

    sphericalmeasurementmotionmanager = SphericalMeasurementMotionManager(mock_scanner, mock_measurement_points)

    radius = 10.0
    mock_measurement_points.get_radius.return_value = radius

    sphericalmeasurementmotionmanager.move_to_safe_starting_position()

    mock_measurement_points.get_radius.assert_called_once()
    mock_scanner.planar_move_to.assert_called_once_with(radius, 0.0)

def test_next():
    mock_scanner = Mock()
    mock_measurement_points = Mock()

    sphericalmeasurementmotionmanager = SphericalMeasurementMotionManager(mock_scanner, mock_measurement_points)

    position_reference = CylindricalPosition(1.0, 2.0, 3.0)
    mock_measurement_points.next.return_value = position_reference

    position = sphericalmeasurementmotionmanager.next()

    assert position == position_reference

def test_ready():
    mock_scanner = Mock()
    mock_measurement_points = Mock()

    sphericalmeasurementmotionmanager = SphericalMeasurementMotionManager(mock_scanner, mock_measurement_points)

    mock_measurement_points.ready.return_value = True

    result = sphericalmeasurementmotionmanager.ready()

    assert result == True