from unittest.mock import Mock, call

from numpy.ma.testutils import assert_equal

from datatypes import CylindricalPosition
from scanner import Scanner


def test_radial_move_to():
    mock_planar_mover = Mock()
    mock_angular_mover = Mock()
    radius = 10.0

    scanner = Scanner(mock_planar_mover, mock_angular_mover)
    scanner.radial_move_to(radius)

    mock_planar_mover.move_to_radial.assert_called_once_with(radius)

def test_planar_move_to():
    mock_planar_mover = Mock()
    mock_angular_mover = Mock()
    r = 10.0
    z = 11.0

    scanner = Scanner(mock_planar_mover, mock_angular_mover)
    scanner.planar_move_to(r, z)

    mock_planar_mover.move_to_rz.assert_called_once_with(r, z)

def test_cw_arc_move_to():
    mock_planar_mover = Mock()
    mock_angular_mover = Mock()
    r = 10.0
    z = 11.0
    radius = 12.0

    scanner = Scanner(mock_planar_mover, mock_angular_mover)
    scanner.cw_arc_move_to(r, z, radius)

    mock_planar_mover.cw_arc_move_to.assert_called_once_with(r, z, radius)

def test_ccw_arc_move_to():
    mock_planar_mover = Mock()
    mock_angular_mover = Mock()
    r = 10.0
    z = 11.0
    radius = 12.0

    scanner = Scanner(mock_planar_mover, mock_angular_mover)
    scanner.ccw_arc_move_to(r, z, radius)

    mock_planar_mover.ccw_arc_move_to.assert_called_once_with(r, z, radius)

def test_angular_move_to():
    mock_planar_mover = Mock()
    mock_angular_mover = Mock()
    angle = 10.0

    scanner = Scanner(mock_planar_mover, mock_angular_mover)
    scanner.angular_move_to(angle)
    mock_angular_mover.move_to.assert_called_once_with(angle)

def test_angular_move_to_no_move():
    mock_planar_mover = Mock()
    mock_angular_mover = Mock()
    angle = 10.0

    scanner = Scanner(mock_planar_mover, mock_angular_mover)
    scanner.angular_move_to(angle)  # results in a move
    scanner.angular_move_to(angle)  # does not result in a move anymore
    mock_angular_mover.move_to.assert_called_once_with(angle)

def test_vertical_move_to():
    mock_planar_mover = Mock()
    mock_angular_mover = Mock()
    z = 10.0

    scanner = Scanner(mock_planar_mover, mock_angular_mover)
    scanner.vertical_move_to(z)  # results in move
    mock_planar_mover.move_to_vertical.assert_called_once_with(z)

def test_vertical_move_to_no_move():
    mock_planar_mover = Mock()
    mock_angular_mover = Mock()
    z = 10.0

    scanner = Scanner(mock_planar_mover, mock_angular_mover)
    scanner.vertical_move_to(z)  # results in a move
    scanner.vertical_move_to(z)  # does not result in a move anymore
    mock_planar_mover.move_to_vertical.assert_called_once_with(z)

def test_get_position():
    mock_planar_mover = Mock()
    mock_angular_mover = Mock()
    r = 9.0
    z = 10.0
    t = 11.0
    reference = CylindricalPosition(r, t, z)

    scanner = Scanner(mock_planar_mover, mock_angular_mover)
    scanner.planar_move_to(r, z)
    scanner.angular_move_to(t)
    mock_planar_mover.move_to_rz.assert_called_once_with(r, z)
    mock_angular_mover.move_to.assert_called_once_with(t)

    assert_equal(scanner.get_position(), reference)

def test_set_as_zero():
    mock_planar_mover = Mock()
    mock_angular_mover = Mock()

    scanner = Scanner(mock_planar_mover, mock_angular_mover)
    scanner.set_as_zero()

    calls = [call(), call()]  # both in init and on line above this one
    mock_planar_mover.set_as_zero.assert_has_calls(calls)
    mock_angular_mover.set_as_zero.assert_has_calls(calls)

def test_shutdown():
    mock_planar_mover = Mock()
    mock_angular_mover = Mock()

    scanner = Scanner(mock_planar_mover, mock_angular_mover)
    scanner.shutdown()

    mock_planar_mover.shutdown.assert_called_once_with()
    mock_angular_mover.shutdown.assert_called_once_with()