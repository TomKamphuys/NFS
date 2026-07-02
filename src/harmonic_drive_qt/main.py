"""Launch the native Qt prototype."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from loguru import logger

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from harmonic_drive_qt.backend import BackendManager, Worker
    from harmonic_drive_qt.main_window import MainWindow
    from harmonic_drive_qt.qt_compat import QApplication, QMessageBox, QThreadPool, QTimer
    from harmonic_drive_qt.styles import app_stylesheet
else:
    from .backend import BackendManager, Worker
    from .main_window import MainWindow
    from .qt_compat import QApplication, QMessageBox, QThreadPool, QTimer
    from .styles import app_stylesheet


def apply_app_style(app: QApplication) -> None:
    app.setStyleSheet(app_stylesheet())


def _startup_warning_is_audio_setup(message: str) -> bool:
    text = str(message).casefold()
    return any(
        marker in text
        for marker in (
            "open audio setup",
            "audio setup",
            "audio device",
            "audio api",
        )
    )


def show_startup_warning(window: MainWindow, backend: BackendManager) -> None:
    if not backend.load_warning:
        return
    is_audio_setup_warning = _startup_warning_is_audio_setup(backend.load_warning)
    QMessageBox.warning(
        window,
        "Audio Setup Warning" if is_audio_setup_warning else "Scanner Startup Warning",
        backend.load_warning,
    )
    if is_audio_setup_warning:
        window.show_audio()
    else:
        window.show_settings("scanner")


def main() -> int:
    parser = argparse.ArgumentParser(description="HALS native Qt prototype")
    parser.add_argument("--config", default="config.ini", help="Path to config.ini")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    apply_app_style(app)
    try:
        from harmonic_drive import project

        project.reset_temporary_project_dir()
        project.set_project_dir(Path(tempfile.gettempdir()) / "HALS_working_project", args.config)
        project.apply_to_config(args.config)
    except Exception:
        logger.exception("Could not initialize project context")

    backend = BackendManager(args.config)
    window = MainWindow(backend, args.config)
    window.show()

    def load_backend_after_first_paint() -> None:
        worker = Worker(backend.load)
        window._startup_load_worker = worker
        worker.signals.finished.connect(lambda: show_startup_warning(window, backend))

        def show_unexpected_load_failure(message: str) -> None:
            if _startup_warning_is_audio_setup(message):
                backend.load_warning = f"Audio setup needs attention:\n\n{message}"
            else:
                backend.load_warning = f"The scanner backend did not load:\n\n{message}"
            show_startup_warning(window, backend)

        worker.signals.failed.connect(show_unexpected_load_failure)
        QThreadPool.globalInstance().start(worker)

    QTimer.singleShot(100, load_backend_after_first_paint)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
