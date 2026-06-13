import os
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from harmonic_drive_qt.audio_setup_pane import AudioSetupPane
from harmonic_drive_qt.qt_compat import QApplication, QSizePolicy
from harmonic_drive_qt.styles import app_stylesheet


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
        lambda _in_dev, _out_dev: [48000],
    )

    pane = AudioSetupPane(Mock(), str(config_file))
    try:
        for combo in (pane.api_select, pane.fs):
            style = combo.styleSheet()
            assert "border: 1px solid #bfc8d4" in style
            assert "border-radius: 4px" in style
            assert combo.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
            longest = max(combo.itemText(index) for index in range(combo.count()))
            assert combo.sizeAdjustPolicy() == combo.SizeAdjustPolicy.AdjustToContents
            assert combo.minimumContentsLength() >= len(longest)
            assert combo.width() == combo.sizeHint().width()
            assert combo.width() < pane.width()

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
    finally:
        pane.cal_timer.stop()
        pane.deleteLater()


def test_app_base_font_uses_points_for_combo_popup_compatibility():
    stylesheet = app_stylesheet()

    assert "QWidget {" in stylesheet
    assert "font-size: 10pt;" in stylesheet
    assert "font-size: 13px;" not in stylesheet
