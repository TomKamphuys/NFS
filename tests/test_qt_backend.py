import harmonic_drive_qt.backend as qt_backend
from harmonic_drive_qt.backend import BackendManager, format_nfs_error, format_scanner_error


class FakeAudio:
    def __init__(self):
        self.calls = []
        self.stopped = False

    def measure_ir(self, position, order_id="NA", save=True):
        self.calls.append((position, order_id, save))
        return {"name": "test.wav", "position": position, "saved": save}

    def play_sine(self, frequency, level, duration):
        self.calls.append(("sine", frequency, level, duration))

    def stop_sine(self):
        self.stopped = True

    def update_sine_level(self, level):
        self.calls.append(("level", level))


def test_format_scanner_error_for_missing_com_port():
    message = format_scanner_error(
        Exception(
            "could not open port 'COM5': "
            "FileNotFoundError(2, 'The system cannot find the file specified.', None, 2)"
        )
    )

    assert message == "Scanner connection failed on COM5. Check the port and controller connection."


def test_format_scanner_error_for_nonresponsive_controller():
    message = format_scanner_error(
        TimeoutError("No GRBL response received after opening the serial port")
    )

    assert message == "Scanner not responding on the selected port. Check the port and controller type."


def test_format_nfs_error_for_audio_setup_failure():
    message = format_nfs_error(
        RuntimeError(
            "Could not find audio device 'RME' on 'ASIO'. "
            "Open Audio Setup and select the audio API and device again."
        )
    )

    assert message == (
        "Audio setup needs attention: Could not find audio device 'RME' on 'ASIO'. "
        "Open Audio Setup and select the audio API and device again."
    )
    assert "Near Field Scanner unavailable" not in message


def test_backend_load_keeps_ui_available_when_scanner_port_missing(monkeypatch):
    def raise_missing_port(_config_file):
        raise Exception("could not open port 'COM5': FileNotFoundError")

    monkeypatch.setattr(qt_backend, "setup_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(qt_backend.ScannerFactory, "create", raise_missing_port)

    manager = BackendManager("config.ini")
    try:
        manager.load()
    finally:
        manager.shutdown()

    assert manager.scanner is None
    assert manager.nfs is None
    assert manager.load_warning == (
        "Scanner connection failed on COM5. Check the port and controller connection."
    )


def test_scanner_command_reports_load_warning_when_scanner_missing():
    manager = BackendManager("config.ini")
    try:
        manager.load_warning = "Scanner not responding on the selected port."

        try:
            manager.scanner_command("home")
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("scanner_command should fail without a scanner")
    finally:
        manager.shutdown()

    assert message == "Scanner not responding on the selected port."


def test_backend_load_reports_audio_setup_warning_without_scanner_label(monkeypatch):
    scanner = object()

    def raise_audio_setup_error(_scanner, _config_file):
        raise RuntimeError(
            "Could not find audio device 'RME' on 'ASIO'. "
            "Open Audio Setup and select the audio API and device again."
        )

    monkeypatch.setattr(qt_backend, "setup_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(qt_backend.ScannerFactory, "create", lambda _config_file: scanner)
    monkeypatch.setattr(qt_backend.NearFieldScannerFactory, "create", raise_audio_setup_error)

    manager = BackendManager("config.ini")
    try:
        manager.load()
    finally:
        manager.shutdown()

    assert manager.scanner is scanner
    assert manager.nfs is None
    assert manager.load_warning.startswith("Audio setup needs attention:")
    assert "Near Field Scanner unavailable" not in manager.load_warning


def test_test_sweep_uses_audio_backend_when_scanner_backend_is_missing(monkeypatch):
    audio = FakeAudio()
    monkeypatch.setattr(qt_backend.AudioFactory, "create", lambda _config_file: audio)

    manager = BackendManager("config.ini")
    try:
        result = manager.test_sweep()
    finally:
        manager.shutdown()

    assert len(audio.calls) == 1
    position, order_id, save = audio.calls[0]
    assert (position.r(), position.t(), position.z()) == (0.0, 0.0, 0.0)
    assert order_id == "TEST"
    assert save is False
    assert result["saved"] is False
    assert manager.preview_ir["name"] == "test.wav"
    assert "preview_created_at" in manager.preview_ir


def test_backend_updates_sine_level_on_active_audio_target(monkeypatch):
    audio = FakeAudio()
    monkeypatch.setattr(qt_backend.AudioFactory, "create", lambda _config_file: audio)

    manager = BackendManager("config.ini")
    try:
        manager.play_sine(1000.0, -20.0, None)
        manager.update_sine_level(-19.0)
    finally:
        manager.shutdown()

    assert ("level", -19.0) in audio.calls
