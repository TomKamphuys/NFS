import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from harmonic_drive_qt import main as qt_main


class FakeWindow:
    def __init__(self):
        self.calls = []

    def show_audio(self):
        self.calls.append(("audio", None))

    def show_settings(self, initial_section=None):
        self.calls.append(("settings", initial_section))


def test_startup_scanner_warning_opens_scanner_settings(monkeypatch):
    warnings = []
    monkeypatch.setattr(qt_main.QMessageBox, "warning", lambda *args: warnings.append(args))
    window = FakeWindow()
    backend = SimpleNamespace(load_warning="Scanner connection failed on COM5. Check the port.")

    qt_main.show_startup_warning(window, backend)

    assert warnings[0][1] == "Scanner Startup Warning"
    assert window.calls == [("settings", "scanner")]


def test_startup_audio_warning_opens_audio_setup(monkeypatch):
    warnings = []
    monkeypatch.setattr(qt_main.QMessageBox, "warning", lambda *args: warnings.append(args))
    window = FakeWindow()
    backend = SimpleNamespace(
        load_warning=(
            "Audio setup needs attention: Could not find audio device "
            "'RME' on 'ASIO'. Open Audio Setup and select the audio API and device again."
        )
    )

    qt_main.show_startup_warning(window, backend)

    assert warnings[0][1] == "Audio Setup Warning"
    assert "Near Field Scanner unavailable" not in warnings[0][2]
    assert window.calls == [("audio", None)]
