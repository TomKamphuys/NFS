import os
import sys
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


def test_headless_diagnostic_runs_before_qapplication(monkeypatch, tmp_path):
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    result_file = tmp_path / "result.txt"
    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"zip")
    import nfs.audio_diagnostic as diagnostic

    monkeypatch.setattr(diagnostic, "run_diagnostic", lambda path, launcher_command=None: archive)
    monkeypatch.setattr(
        qt_main,
        "QApplication",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("QApplication was constructed")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "harmonic-drive-qt",
            "--audio-diagnostic-run",
            str(request),
            "--audio-diagnostic-result-file",
            str(result_file),
        ],
    )

    assert qt_main.main() == 0
    assert result_file.read_text(encoding="utf-8") == str(archive)


def test_fresh_process_group_runs_before_qapplication(monkeypatch, tmp_path):
    import nfs.audio_diagnostic as diagnostic

    calls = []
    monkeypatch.setattr(
        diagnostic,
        "run_diagnostic_group",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        qt_main,
        "QApplication",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("QApplication was constructed")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "harmonic-drive-qt",
            "--audio-diagnostic-group",
            "backend",
            "--audio-diagnostic-request",
            str(tmp_path / "request.json"),
            "--audio-diagnostic-run-dir",
            str(tmp_path),
            "--audio-diagnostic-progress-offset",
            "4",
            "--audio-diagnostic-progress-total",
            "33",
        ],
    )

    assert qt_main.main() == 0
    assert calls[0][0][2] == "backend"
    assert calls[0][1] == {"progress_offset": 4, "progress_total": 33}
