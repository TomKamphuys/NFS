import os
import configparser
from pathlib import Path
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from harmonic_drive_qt.grid_pane import GridGeneratorPane
from harmonic_drive_qt.qt_compat import QApplication
from harmonic_drive_qt import project
from nfs.datatypes import CylindricalPosition
from nfs.motion_manager import MotionManagerFactory, CylindricalNoFlyZone
from nfs import registry


def _app():
    return QApplication.instance() or QApplication([])


def _write_config(path: Path) -> None:
    path.write_text(
        """[app]
coord_viewer_backend = matplotlib

[dut_volume]
top_r = 210.0
top_phi = 40.0
top_z = 320.0
bot_r = 50.0
bot_phi = 10.0
bot_z = -40.0

[sweep]
driver_protection_highpass_freq = 0
level_db = -12.0
sample_rate = 48000
duration = 1.0
padding = 0.1
taper = 0.05
n_sweeps = 1
sweep_dir = sweeps
sweep_filename = sweep.wav
use_marker_alignment = False
marker_freq = 1000
marker_duration = 0.01

[motion_manager]
type = FastCylindricalMeasurementMotionManager
optimize_point_order = False
unsafe_point_policy = abort
measurement_points_type = FileMeasurementPoints
filename = test_grid.csv
""",
        encoding="utf-8",
    )


def test_dut_volume_section_and_slave_synchronization(tmp_path, monkeypatch):
    _app()
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.ini"
    _write_config(config_path)

    backend = Mock()
    backend.get_position.return_value = CylindricalPosition(180.0, 30.0, 290.0)

    pane = GridGeneratorPane(backend, str(config_path))
    try:
        # Verify DUT Volume waypoints loaded from config
        assert [f.text() for f in pane.dut_inputs["top"]] == ["210.0", "40.0", "320.0"]
        assert [f.text() for f in pane.dut_inputs["bottom"]] == ["50.0", "10.0", "-40.0"]

        # Verify slaved Grid Generation waypoints mirror DUT Volume
        assert [f.text() for f in pane.waypoint_inputs["top"]] == ["210.0", "40.0", "320.0"]
        assert [f.text() for f in pane.waypoint_inputs["bottom"]] == ["50.0", "10.0", "-40.0"]
        assert pane.slave_top is True
        assert pane.slave_bottom is True

        # Update DUT Top waypoint and verify automatic slave synchronization
        pane.dut_inputs["top"][0].setText("225.0")
        pane.dut_inputs["top"][0].textEdited.emit("225.0")
        assert pane.waypoint_inputs["top"][0].text() == "225.0"

        # Verify config was updated
        parser = configparser.ConfigParser()
        parser.read(config_path)
        assert parser.get("dut_volume", "top_r") == "225.0"

        # Explicitly edit Grid Generation Top waypoint -> breaks connection
        pane.waypoint_inputs["top"][0].setText("190.0")
        pane.waypoint_inputs["top"][0].textEdited.emit("190.0")
        assert pane.slave_top is False
        assert pane.slave_checkboxes["top"].isChecked() is False

        # Changing DUT Top again should NOT update Grid Generation Top
        pane.dut_inputs["top"][0].setText("300.0")
        pane.dut_inputs["top"][0].textEdited.emit("300.0")
        assert pane.waypoint_inputs["top"][0].text() == "190.0"

        # Re-enabling slave connection updates Grid Generation Top
        pane.slave_checkboxes["top"].setChecked(True)
        assert pane.slave_top is True
        assert pane.waypoint_inputs["top"][0].text() == "300.0"
    finally:
        pane.shutdown()
        pane.sync_timer.stop()
        pane.deleteLater()


def test_motion_manager_factory_reads_dut_volume_no_fly_zone(tmp_path):
    config_path = tmp_path / "config.ini"
    _write_config(config_path)

    from nfs.plugins.file_measurement_points import register as register_file_mp
    register_file_mp(registry.measurement_points)

    scanner = Mock()
    scanner.get_position.return_value = CylindricalPosition(0.0, 0.0, 0.0)

    grid_file = tmp_path / "test_grid.csv"
    grid_file.write_text("r_mm,phi_deg,z_mm\n300,0,100\n", encoding="utf-8")

    mm = MotionManagerFactory.create(str(config_path), "motion_manager", scanner)
    nf = mm._no_fly_zone
    assert isinstance(nf, CylindricalNoFlyZone)
    assert nf.r_wall == 210.0
    assert nf.z_min == -40.0
    assert nf.z_max == 320.0
