import configparser
from unittest.mock import Mock, patch

from nfs.motion_manager import MotionManagerFactory
from nfs.plugins.file_measurement_points import FileMeasurementPoints


def test_file_measurement_points_gaps_default_to_no_extra_filtering(tmp_path):
    grid_file = tmp_path / "scan_path.csv"
    grid_file.write_text(
        "r_xy_mm,phi_deg,z_mm\n"
        "100,180,0\n"
        "250,0,10\n"
    )

    points = FileMeasurementPoints(str(grid_file))

    assert points.total_points() == 2
    assert points.next().r() == 100


def test_cylindrical_motion_manager_defaults_safe_radius_to_zero_when_missing(tmp_path):
    config_file = tmp_path / "config.ini"
    config = configparser.ConfigParser()
    config["motion_manager"] = {
        "type": "CylindricalMeasurementMotionManager",
        "measurement_points": "measurement_points",
    }
    config["measurement_points"] = {"type": "DummyMeasurementPoints"}
    with open(config_file, "w") as f:
        config.write(f)

    scanner = Mock()
    measurement_points = Mock()

    with patch("nfs.motion_manager.factory.create", return_value=measurement_points):
        manager = MotionManagerFactory.create(str(config_file), "motion_manager", scanner)

    manager.move_to_safe_starting_radius()

    scanner.planar_move_to.assert_called_once_with(0.0, 0.0)
    measurement_points.get_radius.assert_not_called()
