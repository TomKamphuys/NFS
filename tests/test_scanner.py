from unittest.mock import Mock, call
import pytest
from nfs.datatypes import CylindricalPosition, GrblMachineState
from nfs.scanner import Scanner


@pytest.fixture
def mock_grbl():
    return Mock()


@pytest.fixture
def scanner(mock_grbl):
    # Initial position is (0,0,0) by default in many mocks, 
    # but let's be explicit if needed.
    mock_grbl.get_position.return_value = CylindricalPosition(0, 0, 0)
    return Scanner(mock_grbl, feed_rate=1000)


def test_radial_move_to(scanner, mock_grbl):
    scanner.radial_move_to(10.0)
    mock_grbl.send_and_wait_for_move_ready.assert_called_once_with('G0 Y10.0000')


def test_radial_move_to_no_move(scanner, mock_grbl):
    mock_grbl.get_position.return_value = CylindricalPosition(10.0, 0, 0)
    scanner.radial_move_to(10.0)
    mock_grbl.send_and_wait_for_move_ready.assert_not_called()


def test_planar_move_to(scanner, mock_grbl):
    scanner.planar_move_to(10.0, 20.0)
    mock_grbl.send_and_wait_for_move_ready.assert_called_once_with('G0 X20.0000 Y10.0000')


def test_cw_arc_move_to(scanner, mock_grbl):
    scanner.cw_arc_move_to(10.0, 20.0, 5.0)
    mock_grbl.send_and_wait_for_move_ready.assert_called_once_with('G02 X20.0000 Y10.0000 R5.0000 F1000')


def test_ccw_arc_move_to(scanner, mock_grbl):
    scanner.ccw_arc_move_to(10.0, 20.0, 5.0)
    mock_grbl.send_and_wait_for_move_ready.assert_called_once_with('G03 X20.0000 Y10.0000 R5.0000 F1000')


def test_angular_move_to(scanner, mock_grbl):
    scanner.angular_move_to(90.0)
    mock_grbl.send_and_wait_for_move_ready.assert_called_once_with('G0 Z90.0')


def test_angular_move_to_no_move(scanner, mock_grbl):
    mock_grbl.get_position.return_value = CylindricalPosition(0, 90.0, 0)
    scanner.angular_move_to(90.0)
    mock_grbl.send_and_wait_for_move_ready.assert_not_called()


def test_vertical_move_to(scanner, mock_grbl):
    scanner.vertical_move_to(50.0)
    mock_grbl.send_and_wait_for_move_ready.assert_called_once_with('G0 X50.0000')


def test_vertical_move_to_no_move(scanner, mock_grbl):
    mock_grbl.get_position.return_value = CylindricalPosition(0, 0, 50.0)
    scanner.vertical_move_to(50.0)
    mock_grbl.send_and_wait_for_move_ready.assert_not_called()


def test_relative_moves(scanner, mock_grbl):
    # We need to simulate the position updating after each move 
    # if the code calls get_position() multiple times.
    # In scanner.py, relative moves call get_position() once at the start.

    # 1. Rotate CCW 5
    mock_grbl.get_position.return_value = CylindricalPosition(10, 20, 30)
    scanner.rotate_ccw(5)  # t = 20 + 5 = 25
    mock_grbl.send_and_wait_for_move_ready.assert_any_call('G0 Z25.0')

    # 2. Rotate CW 10
    mock_grbl.get_position.return_value = CylindricalPosition(10, 25, 30)
    scanner.rotate_cw(10)  # t = 25 - 10 = 15
    mock_grbl.send_and_wait_for_move_ready.assert_any_call('G0 Z15.0')

    # 3. Move Out 5
    mock_grbl.get_position.return_value = CylindricalPosition(10, 15, 30)
    scanner.move_out(5)  # r = 10 + 5 = 15
    mock_grbl.send_and_wait_for_move_ready.assert_any_call('G0 Y15.0000')

    # 4. Move In 10
    mock_grbl.get_position.return_value = CylindricalPosition(15, 15, 30)
    scanner.move_in(10)  # r = 15 - 10 = 5
    mock_grbl.send_and_wait_for_move_ready.assert_any_call('G0 Y5.0000')

    # 5. Move Up 5
    mock_grbl.get_position.return_value = CylindricalPosition(5, 15, 30)
    scanner.move_up(5)  # z = 30 + 5 = 35
    mock_grbl.send_and_wait_for_move_ready.assert_any_call('G0 X35.0000')

    # 6. Move Down 10
    mock_grbl.get_position.return_value = CylindricalPosition(5, 15, 35)
    scanner.move_down(10)  # z = 35 - 10 = 25
    mock_grbl.send_and_wait_for_move_ready.assert_any_call('G0 X25.0000')


def test_get_position(scanner, mock_grbl):
    pos = CylindricalPosition(1, 2, 3)
    mock_grbl.get_position.return_value = pos
    assert scanner.get_position() == pos


def test_get_state(scanner, mock_grbl):
    mock_grbl.get_state.return_value = GrblMachineState.IDLE
    assert scanner.get_state() == GrblMachineState.IDLE
    assert scanner.is_idle() is True

    mock_grbl.get_state.return_value = GrblMachineState.RUN
    assert scanner.is_running() is True

    mock_grbl.get_state.return_value = GrblMachineState.ALARM
    assert scanner.is_alarm() is True


def test_set_as_zero(scanner, mock_grbl):
    scanner.set_as_zero()
    # mock_grbl.force_position_update.assert_called_once()
    mock_grbl.send.assert_has_calls([
        call('G10 L20 P2 X0 Y0 Z0'),
        call('G10 L20 P1 X0 Y0 Z0')
    ])


def test_set_speaker_center_above_stool(scanner, mock_grbl):
    # Current pos in G55 is (r=100, t=0, z=50) -> Y=100, Z=0, X=50
    mock_grbl.get_position.return_value = CylindricalPosition(100, 0, 50)

    scanner.set_speaker_center_above_stool(20.0)

    # Check sequence
    mock_grbl.send.assert_any_call('G55')
    mock_grbl.send.assert_any_call('G4 P0.1')
    mock_grbl.force_position_update.assert_called()

    # height=20, g55_z=50 -> g54_x = 50 - 20 = 30
    mock_grbl.send.assert_any_call('G10 L20 P1 X30.0000 Y100.0000 Z0.0000')
    mock_grbl.send.assert_any_call('G54')


def test_control_commands(scanner, mock_grbl):
    scanner.shutdown()
    mock_grbl.shutdown.assert_called_once()

    scanner.home()
    mock_grbl.send_and_wait_for_move_ready.assert_called_with('$H')

    scanner.clear_alarm()
    mock_grbl.killalarm.assert_called_once()

    scanner.softreset()
    mock_grbl.softreset.assert_called_once()

    scanner.hold()
    mock_grbl.hold.assert_called_once()
