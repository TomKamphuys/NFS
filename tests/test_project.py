import configparser
import tempfile
from pathlib import Path

import pytest

from harmonic_drive.control import loaded_grid_file_exists, measurement_outputs_exist
from harmonic_drive import project
from harmonic_drive.gui import NO_SESSION_FOLDER_TEXT, resolve_session_folder_value


def _write_config(path):
    path.write_text(
        "[audio]\n"
        "mode = hardware\n"
        "in_dev = 1\n"
        "out_dev = 2\n"
        "\n"
        "[sweep]\n"
        "sweep_dur_s = 1.5\n"
        "sweep_level_dbfs = -9.0\n"
        "num_sweeps = 2\n"
        "\n"
        "[motion_manager]\n"
        "type = CylindricalMeasurementMotionManager\n",
        encoding="utf-8",
    )


def _write_default_config(path):
    path.write_text(
        "[audio]\n"
        "mode = hardware\n"
        "in_dev = 99\n"
        "out_dev = 98\n"
        "\n"
        "[sweep]\n"
        "sweep_dur_s = 4.5\n"
        "sweep_level_dbfs = -30.0\n"
        "num_sweeps = 7\n"
        "protect_hpf_hz = None\n"
        "protect_hpf_order = 2\n"
        "protect_hpf_correction = False\n"
        "protect_hpf_corr_db_cap = 9.0\n"
        "align_to_first_marker = True\n"
        "pre_sil_ms = 100.0\n"
        "post_sil_ms = 200.0\n"
        "mic_tail_taper_ms = 30.0\n"
        "debug_saves = False\n"
        "h2_test_db = None\n"
        "h3_test_db = None\n",
        encoding="utf-8",
    )


def test_new_project_uses_default_sweep_settings_not_last_config_values(tmp_path):
    config_file = tmp_path / "config.ini"
    default_file = tmp_path / "config_default.ini"
    _write_config(config_file)
    _write_default_config(default_file)

    project.set_project_dir(tmp_path / "new_session", str(config_file))

    data = project.get_project_data()
    assert data["sweep_settings"]["sweep_dur_s"] == "4.5"
    assert data["sweep_settings"]["num_sweeps"] == "7"
    assert "sweep_level_dbfs" not in data["sweep_settings"]


def test_applying_new_project_defaults_preserves_audio_and_output_level(tmp_path):
    config_file = tmp_path / "config.ini"
    default_file = tmp_path / "config_default.ini"
    _write_config(config_file)
    _write_default_config(default_file)

    project.set_project_dir(tmp_path / "new_session", str(config_file))
    project.apply_to_config(str(config_file))

    parser = configparser.ConfigParser()
    parser.read(config_file)

    assert parser.get("audio", "in_dev") == "1"
    assert parser.get("audio", "out_dev") == "2"
    assert parser.get("sweep", "sweep_dur_s") == "4.5"
    assert parser.get("sweep", "num_sweeps") == "7"
    assert parser.get("sweep", "sweep_level_dbfs") == "-9.0"


def test_project_json_only_saved_explicitly(tmp_path):
    config_file = tmp_path / "config.ini"
    work_dir = tmp_path / "work"
    _write_config(config_file)

    project.set_project_dir(work_dir, str(config_file))
    project.update_grid_vars({"output_filename": "draft_grid.csv"})
    project.update_audio_setup(
        {"in_dev": "9"},
        {"sweep_dur_s": "2.0", "sweep_level_dbfs": "-12.0", "num_sweeps": "3"},
    )

    assert not list(work_dir.glob("*_project.json"))

    saved_dir = project.save_project_to(tmp_path / "projects" / "speaker_a", "Speaker A", str(config_file))

    assert saved_dir == (tmp_path / "projects" / "speaker_a").resolve()
    saved_files = list(saved_dir.glob("*_project.json"))
    assert len(saved_files) == 1
    content = saved_files[0].read_text(encoding="utf-8")
    assert "sweep_settings" in content
    assert "audio_settings" not in content
    assert "sweep_level_dbfs" not in content


def test_spl_calibration_saves_stage5_frd_offset(tmp_path):
    config_file = tmp_path / "config.ini"
    work_dir = tmp_path / "work"
    _write_config(config_file)

    project.set_project_dir(work_dir, str(config_file))
    project.update_audio_setup(
        {"in_dev": "9"},
        {"sweep_dur_s": "2.0", "num_sweeps": "3"},
        project.build_spl_calibration(80.0, -30.0),
    )
    saved_dir = project.save_project_to(tmp_path / "projects" / "speaker_a", "Speaker A", str(config_file))

    saved_file = next(saved_dir.glob("*_project.json"))
    content = saved_file.read_text(encoding="utf-8")

    assert '"stage5_vars"' in content
    assert '"frd_db_offset": 110.0' in content
    assert '"spl_db": 80.0' in content
    assert '"reference_input_rms_dbfs": -30.0' in content
    assert '"spl_calibration"' not in content
    assert '"spl_offset_db"' not in content


def test_spl_calibration_update_preserves_project_state_and_stage5_vars(tmp_path):
    project.set_project_dir(tmp_path / "work")
    project.update_grid_vars({"output_filename": "grid.csv"})
    project.get_project_data()["stage5_vars"] = {"other_value": 12}
    project.update_audio_setup(
        {"in_dev": "9"},
        {"sweep_dur_s": "2.0", "num_sweeps": "3"},
    )

    project.update_spl_calibration(project.build_spl_calibration(83.0, -27.5))

    data = project.get_project_data()
    assert data["grid_vars"]["output_filename"] == "grid.csv"
    assert data["sweep_settings"]["sweep_dur_s"] == "2.0"
    assert data["stage5_vars"] == {
        "other_value": 12,
        "frd_db_offset": 110.5,
        "spl_db": 83.0,
        "reference_input_rms_dbfs": -27.5,
    }
    assert "spl_calibration" not in data


def test_spl_calibration_can_be_saved_from_known_scale(tmp_path):
    project.set_project_dir(tmp_path / "work")

    project.update_spl_calibration(
        project.build_spl_calibration(None, None, 109.75)
    )

    data = project.get_project_data()
    assert data["stage5_vars"] == {
        "frd_db_offset": 109.75,
        "spl_db": None,
        "reference_input_rms_dbfs": None,
    }
    assert "spl_calibration" not in data


def test_spl_calibration_can_include_meter_weighting(tmp_path):
    project.set_project_dir(tmp_path / "work")

    project.update_spl_calibration(
        project.build_spl_calibration(80.0, -30.0, spl_meter_weighting="C")
    )

    assert project.get_project_data()["stage5_vars"]["spl_meter_weighting"] == "C"


def test_spl_calibration_truncates_frd_offset_to_two_decimals(tmp_path):
    project.set_project_dir(tmp_path / "work")

    project.update_spl_calibration(
        project.build_spl_calibration(None, None, 99.2468908428665)
    )

    data = project.get_project_data()
    assert data["stage5_vars"] == {
        "frd_db_offset": 99.24,
        "spl_db": None,
        "reference_input_rms_dbfs": None,
    }


def test_voltage_calibration_builds_output_and_spl_calibration():
    calibration = project.build_voltage_calibration(
        -20.0,
        0.25,
        26.0,
        -30.0,
        12.5,
    )

    assert calibration["output_voltage_calibration"] == {
        "output_level_dbfs": -20.0,
        "output_vrms": 0.25,
        "amplifier_gain_db": 26.0,
    }
    assert calibration["voltage_calibration"] == {
        "microphone_sensitivity_mv_pa": 12.5,
        "reference_input_rms_dbfs": -30.0,
        "reference_input_vrms": 0.25,
    }
    assert calibration["spl_meter_weighting"] == "Unweighted"
    assert calibration["input_calibration_method"] == "voltage"
    assert calibration["spl_db"] == pytest.approx(120.0205999)
    assert calibration["frd_db_offset"] == 150.02


def test_system_calibration_is_sticky_and_input_method_replaces_stale_details(tmp_path):
    config_file = tmp_path / "config.ini"
    _write_config(config_file)

    project.update_system_calibration(
        str(config_file),
        project.build_voltage_calibration(-20.0, 0.25, 26.0, -30.0, 12.5),
        replace_input=True,
    )
    project.update_system_calibration(
        str(config_file),
        {
            **(project.build_spl_calibration(82.0, -27.5, spl_meter_weighting="C") or {}),
            "input_calibration_method": "spl_meter",
        },
        replace_input=True,
    )

    calibration = project.get_system_calibration(str(config_file))
    assert calibration["output_voltage_calibration"] == {
        "output_level_dbfs": -20.0,
        "output_vrms": 0.25,
        "amplifier_gain_db": 26.0,
    }
    assert "voltage_calibration" not in calibration
    assert calibration["input_calibration_method"] == "spl_meter"
    assert calibration["frd_db_offset"] == 109.5


def test_system_calibration_is_only_copied_to_project_when_explicitly_applied(tmp_path):
    config_file = tmp_path / "config.ini"
    _write_config(config_file)
    project.set_project_dir(tmp_path / "work", str(config_file))
    project.update_spl_calibration(
        {
            "frd_db_offset": 88.0,
            "spl_db": 78.0,
            "reference_input_rms_dbfs": -10.0,
            "spl_meter_weighting": "A",
        }
    )
    project.update_system_calibration(
        str(config_file),
        {
            **(project.build_spl_calibration(82.0, -27.5, spl_meter_weighting="C") or {}),
            "input_calibration_method": "spl_meter",
        },
        replace_input=True,
    )

    saved_dir = project.save_project_to(tmp_path / "manual" / "speaker_a", "Speaker A", str(config_file))
    manual_content = next(saved_dir.glob("*_project.json")).read_text(encoding="utf-8")
    assert '"frd_db_offset": 88.0' in manual_content
    assert '"spl_meter_weighting": "A"' in manual_content

    project.apply_system_calibration_to_project(str(config_file))
    saved_dir = project.save_project_to(tmp_path / "measurement" / "speaker_a", "Speaker A", str(config_file))
    measurement_content = next(saved_dir.glob("*_project.json")).read_text(encoding="utf-8")
    assert '"frd_db_offset": 109.5' in measurement_content
    assert '"spl_meter_weighting": "C"' in measurement_content
    assert '"input_calibration_method": "spl_meter"' in measurement_content


def test_project_apply_does_not_overwrite_global_audio(tmp_path):
    config_file = tmp_path / "config.ini"
    _write_config(config_file)

    project.set_project_dir(tmp_path / "work", str(config_file))
    project.update_audio_setup(
        {"in_dev": "99"},
        {"sweep_dur_s": "3.5", "num_sweeps": "4"},
    )
    project.apply_to_config(str(config_file))

    parser = configparser.ConfigParser()
    parser.read(config_file)

    assert parser.get("audio", "in_dev") == "1"
    assert parser.get("sweep", "sweep_dur_s") == "3.5"


def test_user_positions_are_saved_in_grid_vars(tmp_path):
    project.set_project_dir(tmp_path / "work")
    project.update_grid_vars(
        {
            "user_positions": [{"name": "woofer", "r": 90.0, "phi": 0.0, "z": 80.0}],
        }
    )

    grid_vars = project.get_project_data()["grid_vars"]
    assert grid_vars["user_positions"] == [
        {"name": "woofer", "r": 90.0, "phi": 0.0, "z": 80.0}
    ]


def test_single_measurements_do_not_count_as_full_measurement_output(tmp_path):
    single_dir = tmp_path / "single_measurements"
    single_dir.mkdir(parents=True)
    (single_dir / "single_ir.wav").write_text("audio")

    assert measurement_outputs_exist(tmp_path) is False

    measurement_set_dir = tmp_path / "measurement_set"
    measurement_set_dir.mkdir()
    (measurement_set_dir / "full_ir.wav").write_text("audio")

    assert measurement_outputs_exist(tmp_path) is True


def test_saving_project_does_not_change_default_project_dir(tmp_path):
    config_file = tmp_path / "config.ini"
    default_dir = tmp_path / "HALS_Projects"
    selected_folder = default_dir / "speaker_tweeter"
    _write_config(config_file)
    config_file.write_text(
        config_file.read_text(encoding="utf-8")
        + "\n[app]\n"
        + f"default_project_dir = {default_dir}\n",
        encoding="utf-8",
    )

    project.set_project_dir(tmp_path / "work", str(config_file))
    project.save_project_to(selected_folder, "speaker_tweeter", str(config_file))

    assert project.get_default_project_root(str(config_file)) == default_dir.resolve()


def test_loaded_grid_file_must_exist(tmp_path):
    config_file = tmp_path / "config.ini"
    _write_config(config_file)
    project.set_project_dir(tmp_path, str(config_file))
    project.update_grid_vars({"output_filename": "missing_grid.csv"})

    assert loaded_grid_file_exists(tmp_path) is False

    (tmp_path / "missing_grid.csv").write_text("r_xy_mm,phi_deg,z_mm\n")

    assert loaded_grid_file_exists(tmp_path) is True


def test_temporary_project_dir_detection(tmp_path):
    project.set_project_dir(Path(tempfile.gettempdir()) / "HALS_working_project")

    assert project.is_temporary_project_dir() is True

    project.set_project_dir(tmp_path / "session")

    assert project.is_temporary_project_dir() is False


def test_temporary_project_dir_is_cleared_on_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(project.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(project, "TEMP_PROJECT_DIR", tmp_path / "HALS_working_project")
    temp_project = project.TEMP_PROJECT_DIR
    temp_project.mkdir()
    (temp_project / "stale_project.json").write_text("{}", encoding="utf-8")
    (temp_project / "stale_grid.csv").write_text("r_xy_mm,phi_deg,z_mm\n", encoding="utf-8")

    project.reset_temporary_project_dir()

    assert temp_project.is_dir()
    assert list(temp_project.iterdir()) == []


def test_no_session_placeholder_with_temp_project_is_not_selected(tmp_path):
    value = resolve_session_folder_value(
        NO_SESSION_FOLDER_TEXT,
        Path(tempfile.gettempdir()) / "HALS_working_project",
        is_temporary_project_dir=True,
    )

    assert value is None


def test_no_session_placeholder_uses_activated_project_folder(tmp_path):
    session_dir = tmp_path / "session"
    value = resolve_session_folder_value(
        NO_SESSION_FOLDER_TEXT,
        session_dir,
        is_temporary_project_dir=False,
    )

    assert value == str(session_dir.resolve())


def test_displayed_session_folder_is_selected_even_before_activation(tmp_path):
    session_dir = tmp_path / "picked_session"
    value = resolve_session_folder_value(
        str(session_dir),
        Path(tempfile.gettempdir()) / "HALS_working_project",
        is_temporary_project_dir=True,
    )

    assert value == str(session_dir)
