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
        """[nfs]
audio = audio
plugins = plugins
motion_manager = motion_manager

[audio]
mode = mock_interface
fs = 48000
blocksize = 2048
in_ch_mic = 0
in_ch_loop = 1
out_ch_spkr = 0
out_ch_ref = 1
wasapi_exclusive = True

[plugins]

[app]
coord_viewer_backend = matplotlib

[dut_volume]
top_r = 210.0
top_phi = 40.0
top_z = 320.0
bot_r = 50.0
bot_phi = 10.0
bot_z = -40.0

[sweep]
naming_convention = dimitri
sweep_dur_s = 1.0
sweep_level_dbfs = -12.0
num_sweeps = 1
protect_hpf_hz = None
protect_hpf_order = 1
protect_hpf_correction = True
protect_hpf_corr_db_cap = 12.0
align_to_first_marker = False
pre_sil_ms = 100.0
post_sil_ms = 100.0
mic_tail_taper_ms = 20.0
debug_saves = False
h2_test_db = None
h3_test_db = None

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


def test_backend_reload_nfs_updates_no_fly_zone(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    _write_config(config_path)

    from nfs.plugins.file_measurement_points import register as register_file_mp
    register_file_mp(registry.measurement_points)

    grid_file = tmp_path / "test_grid.csv"
    grid_file.write_text("r_mm,phi_deg,z_mm\n300,0,100\n", encoding="utf-8")

    from harmonic_drive_qt.backend import BackendManager
    backend = BackendManager(str(config_path))
    scanner_mock = Mock()
    scanner_mock.get_position.return_value = CylindricalPosition(300.0, 0.0, 100.0)
    backend.scanner = scanner_mock
    backend.reload_nfs()

    assert backend.nfs is not None
    assert backend.nfs._measurement_motion_manager._no_fly_zone.r_wall == 210.0

    # Modify DUT volume in config.ini
    parser = configparser.ConfigParser()
    parser.read(config_path)
    parser.set("dut_volume", "top_r", "350.0")
    with open(config_path, "w", encoding="utf-8") as f:
        parser.write(f)

    # Calling reload_nfs or start_measurement_set updates backend.nfs
    backend.reload_nfs()
    assert backend.nfs._measurement_motion_manager._no_fly_zone.r_wall == 350.0


def test_format_measurement_error_for_no_fly_zone():
    from harmonic_drive_qt.backend import format_measurement_error
    from nfs.motion_manager import UnsafeMeasurementPointError

    err = UnsafeMeasurementPointError(
        "Measurement point (227.0mm, -166.1°, -2.0mm) lies inside the no-fly zone; aborting the scan to avoid a collision. Review the no-fly zone settings and the measurement grid."
    )
    info = format_measurement_error(err)

    assert "No-Fly Zone" in info["title"]
    assert "227.0mm, -166.1°, -2.0mm" in info["summary"]
    assert "How to fix" in info["details"]
    assert "DUT Volume" in info["how_to_fix"]


def test_control_pane_handles_failed_progress_event(tmp_path, monkeypatch):
    _app()
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.ini"
    _write_config(config_path)

    from harmonic_drive_qt.control_pane import ControlPane
    from harmonic_drive_qt.backend import BackendManager, format_measurement_error
    from nfs.motion_manager import UnsafeMeasurementPointError

    backend = Mock()
    backend.config_file = str(config_path)
    backend.get_position.return_value = CylindricalPosition(0.0, 0.0, 0.0)
    backend.get_state.return_value = "Idle"
    backend.get_machine_position.return_value = None
    backend.get_measurement_progress.return_value = {"status": "ready", "current": 0, "total": 0}
    backend.is_measurement_set_running.return_value = False
    backend.is_measurement_set_paused.return_value = False

    pane = ControlPane(backend)
    try:
        err = UnsafeMeasurementPointError(
            "Measurement point (227.0mm, -166.1°, -2.0mm) lies inside the no-fly zone; aborting the scan to avoid a collision. Review the no-fly zone settings and the measurement grid."
        )
        err_info = format_measurement_error(err)

        # Mock QMessageBox.exec to avoid opening GUI dialog blocking the test
        modal_shown = []
        with monkeypatch.context() as m:
            m.setattr("harmonic_drive_qt.qt_compat.QMessageBox.exec", lambda self: modal_shown.append(True))
            pane.progress_event.emit({
                "status": "failed",
                "current": 2,
                "total": 10,
                "error": str(err),
                "error_info": err_info,
            })

        assert len(modal_shown) == 1
        assert "Failed" in pane.progress.format()
    finally:
        pane.position_timer.stop()
        pane.progress_timer.stop()
        pane.jog_timer.stop()
        pane.deleteLater()
