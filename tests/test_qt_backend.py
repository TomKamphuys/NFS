import harmonic_drive_qt.backend as qt_backend
from harmonic_drive_qt.backend import BackendManager, format_scanner_error


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
