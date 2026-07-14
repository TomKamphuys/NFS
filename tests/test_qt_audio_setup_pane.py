import os
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from harmonic_drive_qt.audio_setup_pane import AudioSetupPane
from harmonic_drive_qt import project
from harmonic_drive_qt.qt_compat import QApplication, QLabel, QMessageBox, QGroupBox, QSizePolicy, QLocale, QDoubleSpinBox, QAbstractSpinBox
from harmonic_drive_qt.styles import app_stylesheet
from harmonic_drive_qt.styles import toggle_style


def _app():
    return QApplication.instance() or QApplication([])


def test_pyside_double_spin_box_accepts_dot_and_comma_regardless_locale():
    _app()
    original_locale = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.Dutch, QLocale.Country.Netherlands))
    spin = QDoubleSpinBox()
    try:
        spin.setRange(-200.0, 200.0)
        spin.setDecimals(2)

        assert spin.valueFromText("1.25") == 1.25
        assert spin.valueFromText("1,25") == 1.25
        assert spin.textFromValue(1.25) == "1.25"
        spin.lineEdit().setText("1,25")
        spin.lineEdit().textEdited.emit("1,25")
        assert spin.lineEdit().text() == "1.25"
    finally:
        QLocale.setDefault(original_locale)
        spin.deleteLater()


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

        assert pane.hpf.text() == "500.0"
        assert pane.hpf.placeholderText() == ""
    finally:
        pane.cal_timer.stop()
        pane.deleteLater()


def test_audio_pane_saves_comma_decimal_text_fields_as_dot_decimals(tmp_path, monkeypatch):
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

    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        pane.hpf_enable.setChecked(True)
        pane.hpf.setText("600,5")
        pane.h2.setText("-20,5")
        pane.h3.setText("None")
        pane.save_audio_setup(notify=False)

        saved = config_file.read_text(encoding="utf-8")
        assert "protect_hpf_hz = 600.5" in saved
        assert "h2_test_db = -20.5" in saved
        assert "h3_test_db = None" in saved
    finally:
        pane.auto_apply_timer.stop()
        pane.cal_timer.stop()
        pane.deleteLater()


def test_audio_spin_edits_save_only_after_editing_finished(tmp_path, monkeypatch):
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
    backend = Mock()

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000],
    )

    pane = AudioSetupPane(backend, str(config_file))
    try:
        backend.load.reset_mock()
        pane.sweep_dur.setValue(2.0)
        assert "sweep_dur_s = 1.0" in config_file.read_text(encoding="utf-8")
        backend.load.assert_not_called()

        pane.sweep_dur.editingFinished.emit()

        assert "sweep_dur_s = 2.0" in config_file.read_text(encoding="utf-8")
        backend.load.assert_called_once()
    finally:
        pane.auto_apply_timer.stop()
        pane.cal_timer.stop()
        pane.deleteLater()


def test_output_level_spin_arrows_save_without_reload_and_update_running_sine(tmp_path, monkeypatch):
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
    backend = Mock()

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000],
    )

    pane = AudioSetupPane(backend, str(config_file))
    try:
        backend.load.reset_mock()
        pane.sine_running = True

        assert pane.level.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.UpDownArrows
        assert pane.level.singleStep() == 1.0
        assert pane.level.objectName() == "OutputLevelSpin"
        assert "spin-chevron-up.svg" in pane.level.styleSheet()
        pane.level.stepBy(1)

        assert pane.level.value() == -19.0
        backend.update_sine_level.assert_called_once_with(-19.0)
        backend.load.assert_not_called()
        assert "sweep_level_dbfs = -19.0" in config_file.read_text(encoding="utf-8")
    finally:
        pane.auto_apply_timer.stop()
        pane.cal_timer.stop()
        pane.deleteLater()


def test_audio_pane_prefers_saved_name_and_hostapi_over_stale_id(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[audio]\n"
        "in_dev = 4\n"
        "out_dev = 4\n"
        "in_ch_mic = 0\n"
        "in_ch_loop = 1\n"
        "out_ch_spkr = 0\n"
        "out_ch_ref = 1\n"
        "fs = 48000\n"
        "blocksize = 2048\n"
        "wasapi_exclusive = False\n"
        "in_dev_name = RME Interface\n"
        "in_dev_hostapi = ASIO\n"
        "out_dev_name = RME Interface\n"
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
        4: {
            "name": "RME Interface",
            "hostapi": "MME",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
        20: {
            "name": "RME Interface",
            "hostapi": "ASIO",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
    }

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000],
    )

    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        assert pane.api_select.currentText() == "ASIO"
        assert pane.selected_device_id("in") == 20
        assert pane.selected_device_id("out") == 20
    finally:
        pane.cal_timer.stop()
        pane.deleteLater()


def test_audio_pane_resets_channels_to_first_then_second_on_api_change(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[audio]\n"
        "in_ch_mic = 1\n"
        "in_ch_loop = 0\n"
        "out_ch_spkr = 1\n"
        "out_ch_ref = 0\n"
        "fs = 48000\n"
        "blocksize = 2048\n"
        "wasapi_exclusive = False\n"
        "in_dev_name = Direct Device\n"
        "in_dev_hostapi = Windows DirectSound\n"
        "out_dev_name = Direct Device\n"
        "out_dev_hostapi = Windows DirectSound\n"
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
        4: {
            "name": "Direct Device",
            "hostapi": "Windows DirectSound",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
        5: {
            "name": "ASIO Device",
            "hostapi": "ASIO",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
    }

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000],
    )

    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        assert pane.in_mic_channel.currentData() == 1
        assert pane.in_loop_channel.currentData() == 0

        pane._set_combo_data(pane.api_select, "ASIO")
        pane.refresh_devices_for_api()

        assert pane.in_mic_channel.currentData() == 0
        assert pane.in_loop_channel.currentData() == 1
        assert pane.out_speaker_channel.currentData() == 0
        assert pane.out_ref_channel.currentData() == 1
    finally:
        pane.auto_apply_timer.stop()
        pane.cal_timer.stop()
        pane.deleteLater()


def test_audio_pane_prefers_48khz_when_api_changes(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[audio]\n"
        "in_ch_mic = 0\n"
        "in_ch_loop = 1\n"
        "out_ch_spkr = 0\n"
        "out_ch_ref = 1\n"
        "fs = 96000\n"
        "blocksize = 2048\n"
        "wasapi_exclusive = False\n"
        "in_dev_name = Direct Device\n"
        "in_dev_hostapi = Windows DirectSound\n"
        "out_dev_name = Direct Device\n"
        "out_dev_hostapi = Windows DirectSound\n"
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
        4: {
            "name": "Direct Device",
            "hostapi": "Windows DirectSound",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
        5: {
            "name": "ASIO Device",
            "hostapi": "ASIO",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
    }

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000, 96000],
    )

    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        assert pane.fs.currentData() == 96000

        pane._set_combo_data(pane.api_select, "ASIO")
        pane.refresh_devices_for_api()

        assert pane.fs.currentData() == 48000
    finally:
        pane.auto_apply_timer.stop()
        pane.cal_timer.stop()
        pane.deleteLater()


def test_disabled_protection_hpf_shows_500hz_default_and_saves_when_enabled(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[audio]\n"
        "in_ch_mic = 0\n"
        "in_ch_loop = 1\n"
        "out_ch_spkr = 0\n"
        "out_ch_ref = 1\n"
        "fs = 48000\n"
        "blocksize = 2048\n"
        "wasapi_exclusive = False\n"
        "in_dev_name = ASIO Device\n"
        "in_dev_hostapi = ASIO\n"
        "out_dev_name = ASIO Device\n"
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
        5: {
            "name": "ASIO Device",
            "hostapi": "ASIO",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
    }

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000],
    )

    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        assert pane.hpf.text() == "500.0"
        assert not pane.hpf.isEnabled()

        pane.hpf_enable.setChecked(True)

        assert pane.hpf.text() == "500.0"
        assert pane.hpf.isEnabled()
        assert "protect_hpf_hz = 500.0" in config_file.read_text(encoding="utf-8")
    finally:
        pane.auto_apply_timer.stop()
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
        assert "stage5_vars" not in data
        assert project.get_system_calibration(str(config_file)) == {
            "frd_db_offset": 109.75,
            "spl_db": 82.0,
            "reference_input_rms_dbfs": -27.5,
            "spl_meter_weighting": "C",
            "input_calibration_method": "spl_meter",
        }
        assert not list(session_dir.glob("*_project.json"))
        content = config_file.read_text(encoding="utf-8")
        assert "sweep_level_dbfs = -12.0" in content
        assert "in_dev = 1" in content
        assert "out_dev = 2" in content
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
    project.update_system_calibration(
        str(config_file),
        {
            "frd_db_offset": 109.75,
            "spl_db": 82.0,
            "reference_input_rms_dbfs": -27.5,
            "spl_meter_weighting": "A",
            "input_calibration_method": "spl_meter",
        },
        replace_input=True,
    )
    project.update_spl_calibration(
        {
            "frd_db_offset": 120.0,
            "spl_db": 90.0,
            "reference_input_rms_dbfs": -30.0,
            "spl_meter_weighting": "C",
        }
    )

    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        assert pane.spl_offset.value() == 109.75
        assert pane.spl_reading.value() == 82.0
        assert pane.cal_weighting.currentData() == "a_weighted_inputs"
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
        "inputs": [{"peak_dbfs": -90.0, "rms_dbfs": -95.0}, {"peak_dbfs": -12.0, "rms_dbfs": -15.0}],
        "a_weighted_inputs": [{"peak_dbfs": -90.0, "rms_dbfs": -95.0}, {"peak_dbfs": -22.0, "rms_dbfs": -25.0}],
        "c_weighted_inputs": [{"peak_dbfs": -90.0, "rms_dbfs": -95.0}, {"peak_dbfs": -17.0, "rms_dbfs": -20.0}],
    }

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000],
    )
    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_audio_meter_state", lambda: meter_state)

    project.set_project_dir(tmp_path / "session", str(config_file))
    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        assert pane.cal_weighting.currentData() == "c_weighted_inputs"
        for _ in range(5):
            pane.refresh_cal_meter()
        assert pane.cal_label.text() == "Mic RMS\ndBFS(C)"
        assert pane.held_cal_level_dbfs == -20.0
        assert pane.cal_level.text() == "-20.0 dBFS"
        assert "stage5_vars" not in project.get_project_data()

        pane._set_combo_data(pane.cal_weighting, "a_weighted_inputs")
        for _ in range(5):
            pane.refresh_cal_meter()
        assert pane.cal_label.text() == "Mic RMS\ndBFS(A)"
        assert pane.held_cal_level_dbfs == -25.0
        assert pane.cal_level.text() == "-25.0 dBFS"
        assert "stage5_vars" not in project.get_project_data()

        pane._set_combo_data(pane.cal_weighting, "inputs")
        for _ in range(5):
            pane.refresh_cal_meter()
        assert pane.cal_label.text() == "Mic RMS\ndBFS Unweighted"
        assert pane.held_cal_level_dbfs == -15.0
        assert pane.cal_level.text() == "-15.0 dBFS"
        assert "stage5_vars" not in project.get_project_data()
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

        assert not (session_dir / "Speaker_A_project.json").exists()
        assert project.get_system_calibration(str(config_file)) == {
            "frd_db_offset": 109.75,
            "spl_db": 82.0,
            "reference_input_rms_dbfs": -27.5,
            "spl_meter_weighting": "C",
            "input_calibration_method": "spl_meter",
        }
    finally:
        pane.auto_apply_timer.stop()
        pane.cal_timer.stop()
        pane.deleteLater()


def test_voltage_calibration_can_save_output_only_then_input_spl(tmp_path, monkeypatch):
    _app()
    config_file = tmp_path / "config.ini"
    config_file.write_text(
        "[audio]\n"
        "in_dev = 1\n"
        "out_dev = 2\n"
        "in_ch_mic = 1\n"
        "in_ch_loop = 0\n"
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
        "inputs": [{"peak_dbfs": -90.0, "rms_dbfs": -95.0}, {"peak_dbfs": -12.0, "rms_dbfs": -30.0}],
        "c_weighted_inputs": [{"peak_dbfs": -90.0, "rms_dbfs": -95.0}, {"peak_dbfs": -12.0, "rms_dbfs": -30.0}],
    }

    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_devices_and_channels", lambda: catalog)
    monkeypatch.setattr(
        "harmonic_drive_qt.audio_setup_pane.get_supported_sample_rates",
        lambda _in_dev, _out_dev: [48000],
    )
    monkeypatch.setattr("harmonic_drive_qt.audio_setup_pane.get_audio_meter_state", lambda: meter_state)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Save,
    )

    session_dir = tmp_path / "session"
    project.set_project_dir(session_dir, str(config_file))
    project.set_project_name("Voltage Speaker")
    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        assert not any("Measured output" in group.toolTip() for group in pane.findChildren(QGroupBox))
        assert any(
            label.text() == "?" and "Measured output" in label.toolTip()
            for label in pane.findChildren(QLabel)
        )
        assert pane.voltage_input_label.text() == "Input RMS\ndBFS"
        assert "#831843" in pane.voltage_input_label.styleSheet()

        pane.level.setValue(-20.0)
        pane.voltage_output_vrms.setValue(0.25)
        pane.save_output_voltage_calibration()

        data = project.get_system_calibration(str(config_file))
        assert data["output_voltage_calibration"] == {
            "output_level_dbfs": -20.0,
            "output_vrms": 0.25,
        }
        assert "voltage_calibration" not in data
        assert "250 mVrms interface output" in pane.sweep_level_voltage.text()
        assert "speaker input" not in pane.sweep_level_voltage.text()

        pane.level.setValue(-10.0)
        assert pane.sweep_level_voltage.text() == "791 mVrms interface output"

        pane.level.setValue(-20.0)
        pane.voltage_amp_gain.setValue(26.0)
        pane.save_output_voltage_calibration()
        pane.level.setValue(-10.0)
        assert pane.sweep_level_voltage.text() == (
            "791 mVrms interface output / 15.77 Vrms speaker input"
        )
        pane.level.setValue(-20.0)

        for _ in range(5):
            pane.refresh_cal_meter()
        pane.mic_sensitivity.setValue(12.5)
        pane.save_voltage_spl_calibration()

        data = project.get_system_calibration(str(config_file))
        assert data["output_voltage_calibration"]["amplifier_gain_db"] == 26.0
        assert data["voltage_calibration"] == {
            "microphone_sensitivity_mv_pa": 12.5,
            "reference_input_rms_dbfs": -30.0,
            "reference_input_vrms": 0.25,
        }
        assert data["spl_meter_weighting"] == "Unweighted"
        assert data["frd_db_offset"] == 150.02
        assert pane.cal_weighting.currentData() == "inputs"
        assert not (session_dir / "Voltage_Speaker_project.json").exists()
    finally:
        pane.auto_apply_timer.stop()
        pane.cal_timer.stop()
        pane.deleteLater()
