from unittest.mock import Mock, call
from nfs.datatypes import CylindricalPosition
from nfs.motion_manager import CylindricalMeasurementMotionManager

def test_move_to_safe_starting_radius():
    scanner = Mock()
    measurement_points = Mock()
    # In current implementation it is hardcoded to 300
    
    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points)
    motion_manager.move_to_safe_starting_radius()
    
    scanner.planar_move_to.assert_called_once_with(300, 0.0)

def test_next_simple_planar():
    # Z constant, R changes
    scanner = Mock()
    measurement_points = Mock()
    
    current_pos = CylindricalPosition(100.0, 0.0, 50.0)
    next_pos = CylindricalPosition(150.0, 0.0, 50.0)
    
    scanner.get_position.return_value = current_pos
    measurement_points.next.return_value = next_pos
    measurement_points.get_radius.return_value = 200.0

    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points)
    result = motion_manager.next()

    assert result == next_pos
    # Angular move: no change (0->0)
    scanner.angular_move_to.assert_not_called()
    
    # Planar move: Z constant (diff 0 <= 0.1)
    # Just radial move
    scanner.radial_move_to.assert_called_once_with(150.0)

def test_next_complex_planar():
    # Z changes, R changes. Current R < Safe R
    scanner = Mock()
    measurement_points = Mock()
    
    current_pos = CylindricalPosition(100.0, 0.0, 50.0)
    next_pos = CylindricalPosition(150.0, 0.0, 100.0)
    
    scanner.get_position.return_value = current_pos
    measurement_points.next.return_value = next_pos
    measurement_points.get_radius.return_value = 200.0

    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points)
    motion_manager.next()

    # Angular move: no change
    scanner.angular_move_to.assert_not_called()
    
    # Planar move:
    # Z diff > 0.1
    # 1. Move to safe radius (200.0) because current (100) < safe (200) - tol
    # 2. Move Z to 100.0
    # 3. Move R to 150.0
    
    scanner.radial_move_to.assert_has_calls([call(200.0), call(150.0)])
    scanner.vertical_move_to.assert_called_once_with(100.0)

def test_next_angular_move():
    scanner = Mock()
    measurement_points = Mock()
    
    current_pos = CylindricalPosition(100.0, 0.0, 50.0)
    next_pos = CylindricalPosition(100.0, 90.0, 50.0) # Only angle changes
    
    scanner.get_position.return_value = current_pos
    measurement_points.next.return_value = next_pos
    measurement_points.get_radius.return_value = 200.0

    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points)
    motion_manager.next()

    scanner.angular_move_to.assert_called_once_with(90.0)
    # Planar move checks diffs <= tolerance -> no move
    scanner.radial_move_to.assert_not_called()
    scanner.vertical_move_to.assert_not_called()

def test_ready():
    scanner = Mock()
    measurement_points = Mock()
    measurement_points.ready.return_value = True
    
    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points)
    assert motion_manager.ready() is True

def test_reset():
    scanner = Mock()
    measurement_points = Mock()
    
    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points)
    motion_manager.reset()
    
    measurement_points.reset.assert_called_once()

def test_shutdown():
    scanner = Mock()
    measurement_points = Mock()
    
    motion_manager = CylindricalMeasurementMotionManager(scanner, measurement_points)
    motion_manager.shutdown()
    
    scanner.shutdown.assert_called_once()
