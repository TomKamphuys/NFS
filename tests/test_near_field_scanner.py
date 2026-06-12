from unittest.mock import Mock, patch, mock_open
from pathlib import Path
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
    mocks['audio'].set_session_directory.assert_called_once_with(
        Path.cwd() / "single_measurements"
    )
    mocks['audio'].measure_ir.assert_called_once_with(position)


def test_take_single_measurement_uses_active_project_directory(mocks, tmp_path):
    position = CylindricalPosition(1.0, 2.0, 3.0)
    mocks['scanner'].get_position.return_value = position

    with patch("builtins.open", mock_open()):
        nfs = NearFieldScanner(
            mocks['scanner'],
            mocks['audio'],
            mocks['motion_manager'],
            position_log_file=str(tmp_path / "measurement_positions.csv"),
        )
        nfs.set_project_directory(tmp_path)
        nfs.take_single_measurement()

    mocks['audio'].set_session_directory.assert_called_with(
        tmp_path / "single_measurements"
    )
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
    mocks['motion_manager'].total_points.return_value = 2

    with patch("builtins.open", mock_open()) as mocked_file:
        nfs = NearFieldScanner(mocks['scanner'], mocks['audio'], mocks['motion_manager'])
        nfs.take_measurement_set()

    # Initial call + cleanup call
    assert mocks['motion_manager'].move_to_safe_starting_radius.call_count == 2
    mocks['motion_manager'].reset.assert_called_once()
    mocks['scanner'].angular_move_to.assert_called_once_with(0.0)
    
    # measure_ir called once (first point). Second point loop breaks before measure_ir.
    mocks['audio'].set_session_directory.assert_called_with(Path.cwd() / "measurement_set")
    assert (Path.cwd() / "logs").exists()
    assert mocks['audio'].measure_ir.call_count == 1
    mocks['audio'].measure_ir.assert_called_once_with(pos1)


def test_take_measurement_set_reports_progress(mocks):
    mocks['motion_manager'].ready.side_effect = [
        False,  # first while check
        False,  # after first move
        False,  # second while check
        False,  # after second move
        False,  # third while check
        False,  # after third move
        True,   # exit loop
    ]
    mocks['motion_manager'].total_points.return_value = 4
    pos1 = CylindricalPosition(100, 0, 10)
    pos2 = CylindricalPosition(120, 10, 10)
    pos3 = CylindricalPosition(140, 20, 10)
    mocks['scanner'].get_position.side_effect = [pos1, pos2, pos3]
    progress_events = []

    with patch("builtins.open", mock_open()):
        nfs = NearFieldScanner(mocks['scanner'], mocks['audio'], mocks['motion_manager'])
        nfs.take_measurement_set(progress_callback=progress_events.append)

    assert [(event["status"], event["current"], event["total"]) for event in progress_events] == [
        ("started", 0, 4),
        ("point_complete", 1, 4),
        ("point_complete", 2, 4),
        ("point_complete", 3, 4),
        ("finished", 4, 4),
    ]
    assert all("timestamp" in event for event in progress_events)
    assert progress_events[0]["eta_seconds"] is None
    assert progress_events[1]["eta_seconds"] is None
    assert progress_events[2]["eta_seconds"] is not None
    assert progress_events[2]["eta_seconds"] >= 0
    assert progress_events[3]["eta_seconds"] is not None
    assert progress_events[3]["eta_seconds"] >= 0
    progress = nfs.get_measurement_progress()
    assert progress["status"] == "finished"
    assert progress["current"] == 4
    assert progress["total"] == 4


def test_take_measurement_set_keeps_progress_if_callback_fails(mocks):
    mocks['motion_manager'].ready.side_effect = [
        False,
        False,
        True,
    ]
    mocks['motion_manager'].total_points.return_value = 1
    pos1 = CylindricalPosition(100, 0, 10)
    mocks['scanner'].get_position.return_value = pos1

    def broken_callback(_event):
        raise RuntimeError("browser gone")

    with patch("builtins.open", mock_open()):
        nfs = NearFieldScanner(mocks['scanner'], mocks['audio'], mocks['motion_manager'])
        nfs.take_measurement_set(progress_callback=broken_callback)

    progress = nfs.get_measurement_progress()
    assert progress["status"] == "finished"
    assert progress["current"] == 1
    assert progress["total"] == 1
    assert progress["eta_seconds"] == 0


def test_take_measurement_set_can_overwrite_existing_outputs(mocks, tmp_path):
    measurement_dir = tmp_path / "measurement_set"
    measurement_dir.mkdir(parents=True)
    stale_file = measurement_dir / "stale.txt"
    stale_file.write_text("old")

    mocks['motion_manager'].ready.return_value = True
    mocks['motion_manager'].total_points.return_value = 0

    with patch("builtins.open", mock_open()):
        nfs = NearFieldScanner(
            mocks['scanner'],
            mocks['audio'],
            mocks['motion_manager'],
            position_log_file=str(tmp_path / "measurement_positions.csv"),
        )
        nfs.set_project_directory(tmp_path)
        nfs.take_measurement_set("Woofer", overwrite=True)

    assert measurement_dir.exists()
    assert not stale_file.exists()


def test_measurement_set_stop_finishes_current_point_then_returns_safe(mocks):
    mocks['motion_manager'].ready.side_effect = [
        False,  # first while check
        False,  # after first move
        False,  # next while check, then stop request breaks before next move
    ]
    mocks['motion_manager'].total_points.return_value = 4
    pos1 = CylindricalPosition(100, 0, 10)
    mocks['scanner'].get_position.return_value = pos1

    with patch("builtins.open", mock_open()):
        nfs = NearFieldScanner(mocks['scanner'], mocks['audio'], mocks['motion_manager'])
        mocks['audio'].measure_ir.side_effect = lambda position: nfs.stop_measurement_set()
        nfs.take_measurement_set()

    assert mocks['motion_manager'].next.call_count == 1
    mocks['audio'].measure_ir.assert_called_once_with(pos1)
    assert mocks['motion_manager'].move_to_safe_starting_radius.call_count == 2
    mocks['scanner'].angular_move_to.assert_called_once_with(0.0)
    assert not nfs.is_measurement_set_running()
    progress = nfs.get_measurement_progress()
    assert progress["status"] == "finished"
    assert progress["current"] == 1
    assert progress["total"] == 4


def test_shutdown(mocks):
    with patch("builtins.open", mock_open()):
        nfs = NearFieldScanner(mocks['scanner'], mocks['audio'], mocks['motion_manager'])
        nfs.shutdown()

    mocks['scanner'].shutdown.assert_called_once()
