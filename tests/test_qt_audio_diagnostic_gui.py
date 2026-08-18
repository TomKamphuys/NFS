import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from harmonic_drive_qt import audio_diagnostic_gui
from harmonic_drive_qt.qt_compat import QApplication


def _app():
    return QApplication.instance() or QApplication([])


def test_diagnostic_setup_defaults_to_saved_asio_and_requires_confirmation(tmp_path, monkeypatch):
    _app()
    config = tmp_path / "config.ini"
    config.write_text(
        "[audio]\n"
        "mode = hardware\n"
        "in_dev = 7\n"
        "out_dev = 7\n"
        "in_dev_name = Friend RME\n"
        "in_dev_hostapi = ASIO\n"
        "out_dev_name = Friend RME\n"
        "out_dev_hostapi = ASIO\n"
        "in_ch_loop = 3\n"
        "out_ch_ref = 5\n"
        "fs = 48000\n"
        "blocksize = 512\n",
        encoding="utf-8",
    )
    catalog = {
        2: {
            "name": "Unrelated WASAPI",
            "hostapi": "Windows WASAPI",
            "input_channels": [0, 1],
            "output_channels": [0, 1],
        },
        7: {
            "name": "Friend RME",
            "hostapi": "ASIO",
            "input_channels": list(range(8)),
            "output_channels": list(range(8)),
        },
    }
    monkeypatch.setattr(audio_diagnostic_gui, "get_devices_and_channels", lambda: catalog)

    dialog = audio_diagnostic_gui.AudioDiagnosticSetupDialog(str(config))
    dialog.request.output_root = str(tmp_path / "results")
    try:
        assert dialog.device_combo.currentData() == 7
        assert dialog.input_combo.currentData() == 3
        assert dialog.output_combo.currentData() == 5
        assert dialog.advanced.isHidden()
        assert dialog.start.isEnabled() is False

        dialog.confirm.setChecked(True)
        assert dialog.start.isEnabled() is True
        dialog._accept_setup()
        assert dialog.request_path is not None
        assert dialog.request_path.exists()
    finally:
        dialog.deleteLater()
