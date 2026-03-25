from unittest.mock import Mock, patch, mock_open
import pytest
from nfs.datatypes import CylindricalPosition
from nfs.nfs import NearFieldScanner


@pytest.fixture
def mocks():
    return {
        'scanner': Mock(),
        'audio': Mock(),
        'motion_manager': Mock(),
    }


def test_take_single_measurement(mocks):
    position = CylindricalPosition(1.0, 2.0, 3.0)
    mocks['scanner'].get_position.return_value = position

    with patch("builtins.open", mock_open()):
        nfs = NearFieldScanner(mocks['scanner'], mocks['audio'], mocks['motion_manager'])
        nfs.take_single_measurement()

    mocks['scanner'].get_position.assert_called_once()
    mocks['audio'].measure_ir.assert_called_once_with(position)


def test_take_measurement_set(mocks):
    # Setup motion manager to run for 2 points then be ready
    # Code logic:
    # 1. move_to_safe_starting_radius()
    # 2. while not ready():
    # 3.   next()
    # 4.   if ready(): break
    # 5.   get_position(), log, measure_ir()
    # 6. loop...
    # 7. reset(), move_to_safe_starting_radius(), angular_move_to(0)

    mocks['motion_manager'].ready.side_effect = [
        False, # first while check
        False, # check after first next()
        False, # second while check
        True,  # check after second next() -> BREAK
        True   # (not reached by loop but maybe by other checks)
    ]
    
    pos1 = CylindricalPosition(100, 0, 10)
    mocks['scanner'].get_position.return_value = pos1

    with patch("builtins.open", mock_open()) as mocked_file:
        nfs = NearFieldScanner(mocks['scanner'], mocks['audio'], mocks['motion_manager'])
        nfs.take_measurement_set()

    # Initial call + cleanup call
    assert mocks['motion_manager'].move_to_safe_starting_radius.call_count == 2
    mocks['motion_manager'].reset.assert_called_once()
    mocks['scanner'].angular_move_to.assert_called_once_with(0.0)
    
    # measure_ir called once (first point). Second point loop breaks before measure_ir.
    assert mocks['audio'].measure_ir.call_count == 1
    mocks['audio'].measure_ir.assert_called_once_with(pos1)


def test_shutdown(mocks):
    with patch("builtins.open", mock_open()):
        nfs = NearFieldScanner(mocks['scanner'], mocks['audio'], mocks['motion_manager'])
        nfs.shutdown()

    mocks['scanner'].shutdown.assert_called_once()
