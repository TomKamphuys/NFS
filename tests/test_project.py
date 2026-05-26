import configparser

from harmonic_drive.control import loaded_grid_file_exists, measurement_outputs_exist
from harmonic_drive import project


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
