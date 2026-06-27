import os
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from harmonic_drive_qt.audio_setup_pane import AudioSetupPane
from harmonic_drive import project
from harmonic_drive_qt.qt_compat import QApplication, QLabel, QMessageBox, QSizePolicy
from harmonic_drive_qt.styles import app_stylesheet
from harmonic_drive_qt.styles import toggle_style


def _app():
    return QApplication.instance() or QApplication([])


def test_plain_audio_combos_use_content_sizing_without_custom_popup(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[audio]\n"
        "in_dev = 1\n"
        "out_dev = 2\n"
        "in_ch_mic = 0\n"
        "in_ch_loop = 1\n"
        "out_ch_spkr = 0\n"
        "out_ch_ref = 1\n"
        "fs = 48000\n"
        "blocksize = 2048\n"
        "wasapi_exclusive = False\n"
        "in_dev_hostapi = ASIO\n"
        "out_dev_hostapi = ASIO\n"
        "[sweep]\n"
        "sweep_level_dbfs = -20.0\n"
        "sweep_dur_s = 1.0\n"
        "num_sweeps = 1\n"
        "protect_hpf_hz = None\n"
        "protect_hpf_order = 1\n"
        "protect_hpf_correction = False\n"
        "protect_hpf_corr_db_cap = 12.0\n"
        "naming_convention = dimitri\n"
        "align_to_first_marker = False\n"
        "pre_sil_ms = 0.0\n"
        "post_sil_ms = 0.0\n"
        "mic_tail_taper_ms = 0.0\n"
        "debug_saves = False\n"
        "h2_test_db = None\n"
        "h3_test_db = None\n",
        encoding="utf-8",
    )
    catalog = {
        1: {
            "name": "Input",
            "hostapi": "ASIO",
            "input_channels": [0, 1],
            "output_channels": [],
        },
        2: {
            "name": "Output",
            "hostapi": "ASIO",
            "input_channels": [],
            "output_channels": [0, 1],
        },
    }

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [44100, 48000, 96000],
    )

    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        for combo in (pane.api_select, pane.fs):
            style = combo.styleSheet()
            assert "border: 1px solid #bfc8d4" in style
            assert "border-radius: 4px" in style
            assert "QComboBox::drop-down { border: 0; width: 0px; }" in style
            assert combo.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
            longest = max(combo.itemText(index) for index in range(combo.count()))
            assert combo.sizeAdjustPolicy() == combo.SizeAdjustPolicy.AdjustToContents
            assert combo.minimumContentsLength() >= len(longest)
            assert combo.width() >= combo.sizeHint().width()
            assert combo.width() < pane.width()

        fs_text = "48000 (recommended)"
        assert pane.fs.currentText() == fs_text
        assert pane.fs.width() >= pane.fs.fontMetrics().horizontalAdvance(fs_text) + 24

        for combo in (
            pane.api_select,
            pane.fs,
            pane.in_device,
            pane.out_device,
            pane.in_mic_channel,
            pane.in_loop_channel,
            pane.out_speaker_channel,
            pane.out_ref_channel,
            pane.naming,
        ):
            assert combo.view().parent() is not combo
            combo.showPopup()
            combo.hidePopup()

        assert pane.hpf.text() == ""
        assert pane.hpf.placeholderText() == ""
    finally:
        pane.cal_timer.stop()
        pane.deleteLater()


def test_app_base_font_uses_points_for_combo_popup_compatibility():
    stylesheet = app_stylesheet()

    assert "QWidget {" in stylesheet
    assert "font-size: 10pt;" in stylesheet
    assert "font-size: 13px;" not in stylesheet
    assert "disclosure-closed.svg" in stylesheet
    assert "disclosure-open.svg" in stylesheet
    assert 'QGroupBox::indicator:checked' in stylesheet


def test_checked_disabled_toggle_uses_muted_indicator():
    stylesheet = toggle_style()

    assert "QCheckBox::indicator:checked:disabled" in stylesheet
    assert "toggle-disabled-on.svg" in stylesheet


def test_audio_pane_updates_config_and_project_memory_without_project_json(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[audio]\n"
        "in_dev = 1\n"
        "out_dev = 2\n"
        "in_ch_mic = 0\n"
        "in_ch_loop = 1\n"
        "out_ch_spkr = 0\n"
        "out_ch_ref = 1\n"
        "fs = 48000\n"
        "blocksize = 2048\n"
        "wasapi_exclusive = False\n"
        "in_dev_hostapi = ASIO\n"
        "out_dev_hostapi = ASIO\n"
        "[sweep]\n"
        "sweep_level_dbfs = -20.0\n"
        "sweep_dur_s = 1.0\n"
        "num_sweeps = 1\n"
        "protect_hpf_hz = None\n"
        "protect_hpf_order = 1\n"
        "protect_hpf_correction = False\n"
        "protect_hpf_corr_db_cap = 12.0\n"
        "naming_convention = dimitri\n"
        "align_to_first_marker = False\n"
        "pre_sil_ms = 0.0\n"
        "post_sil_ms = 0.0\n"
        "mic_tail_taper_ms = 0.0\n"
        "debug_saves = False\n"
        "h2_test_db = None\n"
        "h3_test_db = None\n",
        encoding="utf-8",
    )
    catalog = {
        1: {"name": "Input", "hostapi": "ASIO", "input_channels": [0, 1], "output_channels": []},
        2: {"name": "Output", "hostapi": "ASIO", "input_channels": [], "output_channels": [0, 1]},
    }

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000],
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )

    session_dir = tmp_path / "session"
    project.set_project_dir(session_dir, str(config_file))
    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        pane.level.setValue(-12.0)
        pane.sweep_dur.setValue(2.5)
        pane.held_cal_level_dbfs = -27.5
        pane.spl_reading.setValue(82.0)
        pane.spl_offset.setValue(109.75)
        pane.save_spl_calibration()

        data = project.get_project_data()
        assert data["sweep_settings"]["sweep_dur_s"] == "2.5"
        assert "sweep_level_dbfs" not in data["sweep_settings"]
        assert data["stage5_vars"] == {
            "frd_db_offset": 109.75,
            "spl_db": 82.0,
            "reference_input_rms_dbfs": -27.5,
        }
        assert not list(session_dir.glob("*_project.json"))
        assert "sweep_level_dbfs = -12.0" in config_file.read_text(encoding="utf-8")
    finally:
        pane.auto_apply_timer.stop()
        pane.cal_timer.stop()
        pane.deleteLater()


def test_audio_auto_apply_does_not_overwrite_saved_calibration(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[audio]\n"
        "in_dev = 1\n"
        "out_dev = 2\n"
        "in_ch_mic = 0\n"
        "in_ch_loop = 1\n"
        "out_ch_spkr = 0\n"
        "out_ch_ref = 1\n"
        "fs = 48000\n"
        "blocksize = 2048\n"
        "wasapi_exclusive = False\n"
        "in_dev_hostapi = ASIO\n"
        "out_dev_hostapi = ASIO\n"
        "[sweep]\n"
        "sweep_level_dbfs = -20.0\n"
        "sweep_dur_s = 1.0\n"
        "num_sweeps = 1\n"
        "protect_hpf_hz = None\n"
        "protect_hpf_order = 1\n"
        "protect_hpf_correction = False\n"
        "protect_hpf_corr_db_cap = 12.0\n"
        "naming_convention = dimitri\n"
        "align_to_first_marker = False\n"
        "pre_sil_ms = 0.0\n"
        "post_sil_ms = 0.0\n"
        "mic_tail_taper_ms = 0.0\n"
        "debug_saves = False\n"
        "h2_test_db = None\n"
        "h3_test_db = None\n",
        encoding="utf-8",
    )
    catalog = {
        1: {"name": "Input", "hostapi": "ASIO", "input_channels": [0, 1], "output_channels": []},
        2: {"name": "Output", "hostapi": "ASIO", "input_channels": [], "output_channels": [0, 1]},
    }

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000],
    )

    project.set_project_dir(tmp_path / "session", str(config_file))
    project.update_spl_calibration(
        {
            "frd_db_offset": 100.0,
            "spl_db": 80.0,
            "reference_input_rms_dbfs": -20.0,
        }
    )
    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        pane.held_cal_level_dbfs = -30.0
        pane.spl_reading.setValue(90.0)
        pane.spl_offset.setValue(120.0)
        pane.save_audio_setup(notify=False)

        assert project.get_project_data()["stage5_vars"] == {
            "frd_db_offset": 100.0,
            "spl_db": 80.0,
            "reference_input_rms_dbfs": -20.0,
        }
    finally:
        pane.auto_apply_timer.stop()
        pane.cal_timer.stop()
        pane.deleteLater()


def test_audio_pane_loads_saved_calibration_fields_without_live_mic_level(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[audio]\n"
        "in_dev = 1\n"
        "out_dev = 2\n"
        "in_ch_mic = 0\n"
        "in_ch_loop = 1\n"
        "out_ch_spkr = 0\n"
        "out_ch_ref = 1\n"
        "fs = 48000\n"
        "blocksize = 2048\n"
        "wasapi_exclusive = False\n"
        "in_dev_hostapi = ASIO\n"
        "out_dev_hostapi = ASIO\n"
        "[sweep]\n"
        "sweep_level_dbfs = -20.0\n"
        "sweep_dur_s = 1.0\n"
        "num_sweeps = 1\n"
        "protect_hpf_hz = None\n"
        "protect_hpf_order = 1\n"
        "protect_hpf_correction = False\n"
        "protect_hpf_corr_db_cap = 12.0\n"
        "naming_convention = dimitri\n"
        "align_to_first_marker = False\n"
        "pre_sil_ms = 0.0\n"
        "post_sil_ms = 0.0\n"
        "mic_tail_taper_ms = 0.0\n"
        "debug_saves = False\n"
        "h2_test_db = None\n"
        "h3_test_db = None\n",
        encoding="utf-8",
    )
    catalog = {
        1: {"name": "Input", "hostapi": "ASIO", "input_channels": [0, 1], "output_channels": []},
        2: {"name": "Output", "hostapi": "ASIO", "input_channels": [], "output_channels": [0, 1]},
    }

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000],
    )

    project.set_project_dir(tmp_path / "session", str(config_file))
    project.update_spl_calibration(
        {
            "frd_db_offset": 109.75,
            "spl_db": 82.0,
            "reference_input_rms_dbfs": -27.5,
        }
    )

    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        assert pane.spl_offset.value() == 109.75
        assert pane.spl_reading.value() == 82.0
        assert pane.held_cal_level_dbfs is None
        assert pane.cal_level.text() == "-inf dBFS"
    finally:
        pane.auto_apply_timer.stop()
        pane.cal_timer.stop()
        pane.deleteLater()


def test_spl_calibration_weighting_selector_changes_meter_source_and_label(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[audio]\n"
        "in_dev = 1\n"
        "out_dev = 2\n"
        "in_ch_mic = 0\n"
        "in_ch_loop = 1\n"
        "out_ch_spkr = 0\n"
        "out_ch_ref = 1\n"
        "fs = 48000\n"
        "blocksize = 2048\n"
        "wasapi_exclusive = False\n"
        "in_dev_hostapi = ASIO\n"
        "out_dev_hostapi = ASIO\n"
        "[sweep]\n"
        "sweep_level_dbfs = -20.0\n"
        "sweep_dur_s = 1.0\n"
        "num_sweeps = 1\n"
        "protect_hpf_hz = None\n"
        "protect_hpf_order = 1\n"
        "protect_hpf_correction = False\n"
        "protect_hpf_corr_db_cap = 12.0\n"
        "naming_convention = dimitri\n"
        "align_to_first_marker = False\n"
        "pre_sil_ms = 0.0\n"
        "post_sil_ms = 0.0\n"
        "mic_tail_taper_ms = 0.0\n"
        "debug_saves = False\n"
        "h2_test_db = None\n"
        "h3_test_db = None\n",
        encoding="utf-8",
    )
    catalog = {
        1: {"name": "Input", "hostapi": "ASIO", "input_channels": [0, 1], "output_channels": []},
        2: {"name": "Output", "hostapi": "ASIO", "input_channels": [], "output_channels": [0, 1]},
    }
    meter_state = {
        "active": True,
        "inputs": [{"peak_dbfs": -90.0}, {"peak_dbfs": -12.0}],
        "a_weighted_inputs": [{"peak_dbfs": -90.0}, {"peak_dbfs": -22.0}],
        "c_weighted_inputs": [{"peak_dbfs": -90.0}, {"peak_dbfs": -17.0}],
    }

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000],
    )
    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_audio_meter_state", lambda: meter_state)

    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        for _ in range(5):
            pane.refresh_cal_meter()
        assert pane.cal_label.text() == "Mic Level\ndBFS(A)"
        assert pane.held_cal_level_dbfs == -22.0
        assert pane.cal_level.text() == "-22.0 dBFS"

        pane._set_combo_data(pane.cal_weighting, "c_weighted_inputs")
        for _ in range(5):
            pane.refresh_cal_meter()
        assert pane.cal_label.text() == "Mic Level\ndBFS(C)"
        assert pane.held_cal_level_dbfs == -17.0
        assert pane.cal_level.text() == "-17.0 dBFS"

        pane._set_combo_data(pane.cal_weighting, "inputs")
        for _ in range(5):
            pane.refresh_cal_meter()
        assert pane.cal_label.text() == "Mic Level\ndBFS None"
        assert pane.held_cal_level_dbfs == -12.0
        assert pane.cal_level.text() == "-12.0 dBFS"
        assert pane.spl_reading.width() < 100
        assert pane.spl_offset.width() < 100

        assert any("frequency_weighting.png" in label.toolTip() for label in pane.findChildren(QLabel))
    finally:
        pane.auto_apply_timer.stop()
        pane.cal_timer.stop()
        pane.deleteLater()


def test_save_spl_calibration_can_create_missing_project_json_after_prompt(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[audio]\n"
        "in_dev = 1\n"
        "out_dev = 2\n"
        "in_ch_mic = 0\n"
        "in_ch_loop = 1\n"
        "out_ch_spkr = 0\n"
        "out_ch_ref = 1\n"
        "fs = 48000\n"
        "blocksize = 2048\n"
        "wasapi_exclusive = False\n"
        "in_dev_hostapi = ASIO\n"
        "out_dev_hostapi = ASIO\n"
        "[sweep]\n"
        "sweep_level_dbfs = -20.0\n"
        "sweep_dur_s = 1.0\n"
        "num_sweeps = 1\n"
        "protect_hpf_hz = None\n"
        "protect_hpf_order = 1\n"
        "protect_hpf_correction = False\n"
        "protect_hpf_corr_db_cap = 12.0\n"
        "naming_convention = dimitri\n"
        "align_to_first_marker = False\n"
        "pre_sil_ms = 0.0\n"
        "post_sil_ms = 0.0\n"
        "mic_tail_taper_ms = 0.0\n"
        "debug_saves = False\n"
        "h2_test_db = None\n"
        "h3_test_db = None\n",
        encoding="utf-8",
    )
    catalog = {
        1: {"name": "Input", "hostapi": "ASIO", "input_channels": [0, 1], "output_channels": []},
        2: {"name": "Output", "hostapi": "ASIO", "input_channels": [], "output_channels": [0, 1]},
    }

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000],
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Save,
    )

    session_dir = tmp_path / "session"
    project.set_project_dir(session_dir, str(config_file))
    project.set_project_name("Speaker A")
    project.update_grid_vars({"output_filename": "Speaker_A_grid.csv"})
    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        pane.sweep_dur.setValue(2.5)
        pane.held_cal_level_dbfs = -27.5
        pane.spl_reading.setValue(82.0)
        pane.spl_offset.setValue(109.75)
        pane.save_spl_calibration()

        saved_file = session_dir / "Speaker_A_project.json"
        content = saved_file.read_text(encoding="utf-8")
        assert '"frd_db_offset": 109.75' in content
        assert '"spl_db": 82.0' in content
        assert '"reference_input_rms_dbfs": -27.5' in content
        assert '"sweep_dur_s": "2.5"' in content
        assert '"output_filename": "Speaker_A_grid.csv"' in content
        assert "sweep_level_dbfs" not in content
    finally:
        pane.auto_apply_timer.stop()
        pane.cal_timer.stop()
        pane.deleteLater()
