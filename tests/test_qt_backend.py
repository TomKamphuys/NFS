import pytest

pytest.importorskip("PySide6.QtWidgets")

import harmonic_drive_qt.backend as qt_backend
from harmonic_drive_qt.backend import BackendManager, format_nfs_error, format_scanner_error


class FakeNamingConvention:
    def build_ir_path(self, position, session_dir, prefix=None):
        return session_dir / "test_ir.wav"

    def build_dist_path(self, position, session_dir, prefix=None):
        return session_dir / "Distortion" / "test_dist.wav"


class FakeAudio:
    def __init__(self):
        self.calls = []
        self.stopped = False
        self.naming_convention = FakeNamingConvention()

    def capture_ir(self, ir_path, dist_path):
        self.calls.append(("capture", ir_path, dist_path))
        return {"fs": 48000, "ir_linear": [], "ir_full": [], "metrics": {}}

    def preview_ir(self):
        self.calls.append("preview")
        return {"fs": 48000, "ir_linear": [], "ir_full": [], "metrics": {}}

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
    assert audio.calls[0] == "preview"
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


@pytest.mark.parametrize('target', ['sine_audio', 'nfs'])
@pytest.mark.parametrize('action', ['stop', 'shutdown', 'reload'])
def test_continuous_audio_stops_on_starting_thread(monkeypatch, target, action):
    import threading
    events = []

    class OwnedAudio(FakeAudio):
        owner = None

        def play_sine(self, *args):
            self.owner = threading.get_ident()
            assert self.owner != threading.main_thread().ident
            events.append('start')

        def stop_sine(self):
            if self.owner is not None:
                assert threading.get_ident() == self.owner
                events.append('stop')
                self.owner = None

        def shutdown(self):
            assert threading.get_ident() != threading.main_thread().ident
            self.stop_sine()
            events.append('shutdown')

    owned = OwnedAudio()
    manager = BackendManager('config.ini')
    setattr(manager, target, owned)
    monkeypatch.setattr(qt_backend, 'setup_logging', lambda *args, **kwargs: None)
    monkeypatch.setattr(qt_backend.ScannerFactory, 'create', lambda *args: None)
    try:
        manager.play_sine(1000, -30, None)
        if action == 'stop':
            manager.stop_sine()
        elif action == 'reload':
            manager.load()
        else:
            manager.shutdown()
        assert events[:2] == ['start', 'stop']
        assert owned.owner is None
    finally:
        manager.shutdown()
    assert not manager._audio_thread.is_alive()


@pytest.mark.parametrize('operation', ['test', 'single', 'set'])
def test_capture_handoff_closes_sine_on_owner(monkeypatch, operation):
    import threading
    events = []
    manager = BackendManager('config.ini')

    class OwnedAudio(FakeAudio):
        def check(self, event):
            assert threading.current_thread() is manager._audio_thread
            events.append(event)

        def play_sine(self, *args):
            self.check('sine')

        def stop_sine(self):
            self.check('stop')

        def measure_ir(self, *args):
            self.check('capture')
            assert 'stop' in events[events.index('sine') + 1:-1]
            return {}

        def preview_ir(self):
            self.check('capture')
            assert 'stop' in events[events.index('sine') + 1:-1]
            return {}

        test_sweep = measure_ir
        take_single_measurement = measure_ir
        take_measurement_set = measure_ir

    def create(*args):
        assert threading.current_thread() is manager._audio_thread
        events.append('create')
        return OwnedAudio()

    monkeypatch.setattr(qt_backend.AudioFactory, 'create', create)
    try:
        manager.play_sine(1000, -30, None)
        if operation == 'test':
            manager.test_sweep()
        else:
            manager.nfs = OwnedAudio()
            if operation == 'single':
                manager.take_single_measurement()
            else:
                manager.start_measurement_set('test', lambda e: None)
                manager._measurement_thread.join(2)
                assert not manager._measurement_thread.is_alive()
        assert 'capture' in events
    finally:
        manager.shutdown()


def test_reload_constructs_nfs_on_audio_owner(monkeypatch):
    import threading
    manager = BackendManager('config.ini')
    calls = []
    def create(*args):
        assert threading.current_thread() is manager._audio_thread
        calls.append('create')
        return None
    monkeypatch.setattr(qt_backend, 'setup_logging', lambda *a, **k: None)
    monkeypatch.setattr(qt_backend.ScannerFactory, 'create', lambda *a: object())
    monkeypatch.setattr(qt_backend.NearFieldScannerFactory, 'create', create)
    try:
        manager.load()
        manager.load()
        assert calls == ['create', 'create']
    finally:
        manager.shutdown()


@pytest.mark.parametrize('result,releases', [(0, 1), (1, 1), (-2147417850, 0), (-2147467259, 0)])
def test_audio_worker_balances_its_com_reference(monkeypatch, result, releases):
    from types import SimpleNamespace
    from unittest.mock import Mock
    ole = SimpleNamespace(CoInitializeEx=Mock(return_value=result), CoUninitialize=Mock())
    monkeypatch.setattr(qt_backend.sys, 'platform', 'win32')
    monkeypatch.setattr(qt_backend.ctypes, 'windll', SimpleNamespace(ole32=ole), raising=False)
    manager = BackendManager('config.ini')
    try:
        # Worker COM failure must not reject non-hardware or mock jobs.
        # ASIO streams retain their own COM checks when they are opened.
        assert manager._run_audio(lambda: 42) == 42
    finally:
        manager.shutdown()
    ole.CoInitializeEx.assert_called_once_with(None, 2)
    assert ole.CoUninitialize.call_count == releases


@pytest.mark.parametrize("first_operation", ["devices", "rates"])
def test_discovery_dispatches_to_acquisition_process(monkeypatch, first_operation):
    import threading
    from unittest.mock import Mock

    service = Mock()
    manager = BackendManager("config.ini")
    def devices():
        assert threading.current_thread() is manager._audio_thread
        return {0: {"name": "Remote ASIO", "hostapi": "ASIO"}}
    def rates(*args):
        assert threading.current_thread() is manager._audio_thread
        assert args == (0, 0)
        return [48000, 96000]
    service.get_devices_and_channels.side_effect = devices
    service.get_supported_sample_rates.side_effect = rates
    monkeypatch.setattr(qt_backend, "get_audio_process", lambda: service)
    close = Mock()
    monkeypatch.setattr(qt_backend, "shutdown_audio_process", close)
    try:
        if first_operation == "rates":
            assert 96000 in manager.get_audio_sample_rates(0, 0)
        assert manager.get_audio_devices()[0]["name"] == "Remote ASIO"
        assert 48000 in manager.get_audio_sample_rates(0, 0)
        assert manager._run_audio(manager.get_audio_devices)[0]["hostapi"] == "ASIO"
    finally:
        manager.shutdown()
    close.assert_called_once_with()
    assert not manager._audio_thread.is_alive()


def test_direct_reload_constructs_on_owner_and_closes_previous_audio(monkeypatch):
    import threading
    manager = BackendManager("config.ini")
    events = []
    manager.scanner = object()
    def create(*args):
        assert threading.current_thread() is manager._audio_thread
        events.append("create")
        return None
    def stop():
        assert threading.current_thread() is manager._audio_thread
        events.append("stop")
    monkeypatch.setattr(qt_backend.NearFieldScannerFactory, "create", create)
    monkeypatch.setattr(manager, "stop_sine", stop)
    try:
        manager.reload_nfs()
        assert events == ["stop", "create"]
    finally:
        manager.shutdown()


def test_reload_rejects_active_measurement_thread():
    from unittest.mock import Mock
    manager = BackendManager("config.ini")
    manager._measurement_thread = Mock()
    manager._measurement_thread.is_alive.return_value = True
    try:
        with pytest.raises(RuntimeError, match="during a measurement set"):
            manager.reload_nfs()
    finally:
        manager._measurement_thread = None
        manager.shutdown()
