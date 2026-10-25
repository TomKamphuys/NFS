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
    # 4.   get_position(), log, measure_ir()
    # 5. loop...
    # 6. reset(), move_to_park_position()

    mocks['motion_manager'].ready.side_effect = [
        False, # first while check
        False, # after first next() — measure point 1
        True,  # second while check — loop exits (point 2 was already measured)
    ]

    pos1 = CylindricalPosition(100, 0, 10)
    pos2 = CylindricalPosition(110, 5, 10)
    mocks['scanner'].get_position.side_effect = [pos1, pos2]
    mocks['motion_manager'].total_points.return_value = 2

    with patch("builtins.open", mock_open()) as mocked_file:
        nfs = NearFieldScanner(mocks['scanner'], mocks['audio'], mocks['motion_manager'])
        nfs.take_measurement_set()

    # Initial move to safe radius + park position at the end
    assert mocks['motion_manager'].move_to_safe_starting_radius.call_count == 1
    mocks['motion_manager'].move_to_park_position.assert_called_once()
    mocks['motion_manager'].reset.assert_called_once()

    # Both points are measured
    mocks['audio'].set_session_directory.assert_called_with(Path.cwd() / "measurement_set")
    assert (Path.cwd() / "logs").exists()
    assert mocks['audio'].measure_ir.call_count == 2
    mocks['audio'].measure_ir.assert_any_call(pos1)
    mocks['audio'].measure_ir.assert_any_call(pos2)


def test_take_measurement_set_reports_progress(mocks):
    # 4 points: ready returns False for first 3 while-checks, True on 4th
    # Each iteration: next() then measure, then while checks ready()
    mocks['motion_manager'].ready.side_effect = [
        False,  # first while check — enter loop
        False,  # after point 1 measured
        False,  # after point 2 measured
        True,   # after point 3 measured — loop exits
    ]
    mocks['motion_manager'].total_points.return_value = 3
    pos1 = CylindricalPosition(100, 0, 10)
    pos2 = CylindricalPosition(120, 10, 10)
    pos3 = CylindricalPosition(140, 20, 10)
    mocks['scanner'].get_position.side_effect = [pos1, pos2, pos3]
    progress_events = []

    with patch("builtins.open", mock_open()):
        nfs = NearFieldScanner(mocks['scanner'], mocks['audio'], mocks['motion_manager'])
        nfs.take_measurement_set(progress_callback=progress_events.append)

    assert [(event["status"], event["current"], event["total"]) for event in progress_events] == [
        ("started", 0, 3),
        ("point_complete", 1, 3),
        ("point_complete", 2, 3),
        ("point_complete", 3, 3),
        ("finished", 3, 3),
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
    assert progress["current"] == 3
    assert progress["total"] == 3


def test_take_measurement_set_keeps_progress_if_callback_fails(mocks):
    # 1 point: ready returns False at while-check, then True after measuring
    mocks['motion_manager'].ready.side_effect = [
        False,  # while check — enter loop
        True,   # after point 1 measured — loop exits
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
        False,  # after first move — stop requested during measure
        True,   # next while check — stop flag causes break
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
    assert mocks['motion_manager'].move_to_safe_starting_radius.call_count == 1
    mocks['motion_manager'].move_to_park_position.assert_called_once()
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


def test_take_measurement_set_emits_failed_progress_on_unsafe_point(mocks):
    from nfs.motion_manager import UnsafeMeasurementPointError

    mocks['motion_manager'].ready.return_value = False
    mocks['motion_manager'].total_points.return_value = 5
    unsafe_err = UnsafeMeasurementPointError(
        "Measurement point (227.0mm, -166.1°, -2.0mm) lies inside the no-fly zone; aborting the scan to avoid a collision. Review the no-fly zone settings and the measurement grid."
    )
    mocks['motion_manager'].next.side_effect = unsafe_err

    progress_events = []
    with patch("builtins.open", mock_open()):
        nfs = NearFieldScanner(mocks['scanner'], mocks['audio'], mocks['motion_manager'])
        with pytest.raises(UnsafeMeasurementPointError):
            nfs.take_measurement_set(progress_callback=progress_events.append)

    assert any(event["status"] == "failed" for event in progress_events)
    failed_event = [event for event in progress_events if event["status"] == "failed"][0]
    assert "lies inside the no-fly zone" in failed_event["error"]
    assert failed_event["exception"] is unsafe_err
    assert nfs.get_measurement_progress()["status"] == "failed"
