import pytest

pytest.importorskip("PySide6.QtWidgets")

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
def test_discovery_loads_backend_on_com_initialized_audio_owner(monkeypatch, first_operation):
    import threading
    from types import SimpleNamespace
    from nfs import _audio_backend

    events = []
    owner = []

    def initialize(*args):
        owner.append(threading.get_ident())
        events.append("com_init")
        return 0

    def check_owner(event):
        assert threading.get_ident() == owner[0]
        assert threading.get_ident() != threading.main_thread().ident
        assert events[0] == "com_init"
        assert "com_release" not in events
        events.append(event)

    def release():
        check_owner("com_release")

    def query_apis():
        check_owner("devices")
        return [{"name": "ASIO"}]

    def check_settings(**kwargs):
        check_owner("probe")
        assert kwargs["device"] == 0

    device = dict(name="Test ASIO", hostapi=0, max_input_channels=2, max_output_channels=2)
    fake = SimpleNamespace(query_hostapis=query_apis, query_devices=lambda: [device],
                           check_input_settings=check_settings, check_output_settings=check_settings)

    def load(name):
        assert name == "sounddevice"
        check_owner("load")
        return fake

    monkeypatch.setattr(qt_backend.sys, "platform", "win32")
    monkeypatch.setattr(qt_backend.ctypes, "windll", SimpleNamespace(
        ole32=SimpleNamespace(CoInitializeEx=initialize, CoUninitialize=release)), raising=False)
    monkeypatch.setattr(_audio_backend, "import_module", load)
    manager = BackendManager("config.ini")
    try:
        # Creating a worker and doing non-hardware work must remain lazy.
        manager._run_audio(lambda: None)
        assert events == ["com_init"]
        if first_operation == "rates":
            assert 48000 in manager.get_audio_sample_rates(0, 0)
        assert manager.get_audio_devices()[0]["name"] == "Test ASIO"
        assert 48000 in manager.get_audio_sample_rates(0, 0)
        # Nested calls execute directly instead of queuing onto themselves.
        assert manager._run_audio(manager.get_audio_devices)[0]["hostapi"] == "ASIO"
    finally:
        manager.shutdown()
    assert events[-1] == "com_release"
    assert not manager._audio_thread.is_alive()


def test_missing_audio_backend_does_not_break_worker_shutdown(monkeypatch):
    from nfs import _audio_backend

    def unavailable(name):
        raise OSError("PortAudio unavailable")

    monkeypatch.setattr(_audio_backend, "import_module", unavailable)
    manager = BackendManager("config.ini")
    try:
        assert manager.get_audio_devices() == {}
        assert manager.get_audio_sample_rates(0, 0) == []
    finally:
        manager.shutdown()
    assert not manager._audio_thread.is_alive()
