import configparser
import tempfile
from pathlib import Path

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
        "num_sweeps = 2\n"
        "\n"
        "[motion_manager]\n"
        "type = CylindricalMeasurementMotionManager\n",
        encoding="utf-8",
    )


def test_project_json_only_saved_explicitly(tmp_path):
    config_file = tmp_path / "config.ini"
    work_dir = tmp_path / "work"
    _write_config(config_file)

    project.set_project_dir(work_dir, str(config_file))
    project.update_grid_vars({"output_filename": "draft_grid.csv"})
    project.update_audio_setup(
        {"in_dev": "9"},
        {"sweep_dur_s": "2.0", "num_sweeps": "3"},
    )

    assert not list(work_dir.glob("*_project.json"))

    saved_dir = project.save_project_to(tmp_path / "projects" / "speaker_a", "Speaker A", str(config_file))

    assert saved_dir == (tmp_path / "projects" / "speaker_a").resolve()
    saved_files = list(saved_dir.glob("*_project.json"))
    assert len(saved_files) == 1
    content = saved_files[0].read_text(encoding="utf-8")
    assert "sweep_settings" in content
    assert "audio_settings" not in content


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
    assert '"spl_calibration"' not in content
    assert '"spl_offset_db"' not in content
    assert '"spl_db"' not in content
    assert '"reference_input_rms_dbfs"' not in content


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
    assert data["stage5_vars"] == {"other_value": 12, "frd_db_offset": 110.5}
    assert "spl_calibration" not in data


def test_spl_calibration_can_be_saved_from_known_scale(tmp_path):
    project.set_project_dir(tmp_path / "work")

    project.update_spl_calibration(
        project.build_spl_calibration(None, None, 109.75)
    )

    data = project.get_project_data()
    assert data["stage5_vars"] == {"frd_db_offset": 109.75}
    assert "spl_calibration" not in data


def test_spl_calibration_truncates_frd_offset_to_two_decimals(tmp_path):
    project.set_project_dir(tmp_path / "work")

    project.update_spl_calibration(
        project.build_spl_calibration(None, None, 99.2468908428665)
    )

    data = project.get_project_data()
    assert data["stage5_vars"] == {"frd_db_offset": 99.24}


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
