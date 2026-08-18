"""Launch the native Qt prototype."""

from __future__ import annotations

import argparse
import subprocess
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

try:
    if __package__ in {None, ""}:
        from harmonic_drive_qt.wheel_guard import WheelGuard
    else:
        from .wheel_guard import WheelGuard
except ModuleNotFoundError as exc:
    if exc.name != "harmonic_drive_qt.wheel_guard":
        raise
    WheelGuard = None


def apply_app_style(app: QApplication) -> None:
    app.setStyleSheet(app_stylesheet())


def install_wheel_guard(app: QApplication):
    if WheelGuard is None:
        logger.warning("wheel_guard.py is unavailable; mouse-wheel guarding is disabled")
        return None
    wheel_guard = WheelGuard(app)
    app.installEventFilter(wheel_guard)
    return wheel_guard


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


def _self_command() -> list[str]:
    argv0 = Path(sys.argv[0]).resolve()
    if argv0.exists() and argv0.suffix.casefold() == ".exe":
        return [str(argv0)]
    return [sys.executable, "-m", "harmonic_drive_qt.main"]


def main() -> int:
    parser = argparse.ArgumentParser(description="HALS native Qt prototype")
    parser.add_argument("--config", default="config.ini", help="Path to config.ini")
    parser.add_argument(
        "--audio-diagnostic",
        action="store_true",
        help="Open the electrical-loopback diagnostic setup wizard",
    )
    parser.add_argument(
        "--audio-diagnostic-run",
        metavar="REQUEST_JSON",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--audio-diagnostic-result-file",
        metavar="PATH",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--audio-diagnostic-group", help=argparse.SUPPRESS)
    parser.add_argument("--audio-diagnostic-request", help=argparse.SUPPRESS)
    parser.add_argument("--audio-diagnostic-run-dir", help=argparse.SUPPRESS)
    parser.add_argument(
        "--audio-diagnostic-progress-offset", type=int, default=0, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--audio-diagnostic-progress-total", type=int, default=1, help=argparse.SUPPRESS
    )
    args = parser.parse_args()

    # This path intentionally runs before QApplication construction.  The child
    # process doing audio work is therefore headless and never starts Qt's event
    # loop or paints a window.
    if args.audio_diagnostic_group:
        from nfs.audio_diagnostic import run_diagnostic_group

        if not args.audio_diagnostic_request or not args.audio_diagnostic_run_dir:
            parser.error("audio diagnostic group requires its request and run directory")
        run_diagnostic_group(
            args.audio_diagnostic_request,
            args.audio_diagnostic_run_dir,
            args.audio_diagnostic_group,
            progress_offset=args.audio_diagnostic_progress_offset,
            progress_total=args.audio_diagnostic_progress_total,
        )
        return 0

    if args.audio_diagnostic_run:
        from nfs.audio_diagnostic import run_diagnostic

        archive = run_diagnostic(args.audio_diagnostic_run, launcher_command=_self_command())
        if args.audio_diagnostic_result_file:
            Path(args.audio_diagnostic_result_file).write_text(str(archive), encoding="utf-8")
        return 0

    app = QApplication(sys.argv)
    apply_app_style(app)

    if args.audio_diagnostic:
        if __package__ in {None, ""}:
            from harmonic_drive_qt.audio_diagnostic_gui import AudioDiagnosticSetupDialog
        else:
            from .audio_diagnostic_gui import AudioDiagnosticSetupDialog

        dialog = AudioDiagnosticSetupDialog(args.config)
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.request_path is None:
            return 0

        result_pointer = dialog.request_path.with_suffix(".result.txt")
        command = _self_command()
        command.extend(
            [
                "--config",
                args.config,
                "--audio-diagnostic-run",
                str(dialog.request_path),
                "--audio-diagnostic-result-file",
                str(result_pointer),
            ]
        )
        dialog.hide()
        # Device enumeration in the setup window loads PortAudio. Release that
        # instance before the orchestrator starts so its first backend child is
        # a genuinely fresh driver process.
        try:
            import sounddevice as sd

            sd._terminate()
        except Exception:
            logger.exception("Could not release setup-time PortAudio instance")
        # The GUI process waits without an event loop.  The child inherits the
        # terminal and prints coarse progress only after each completed trial.
        completed = subprocess.run(command, check=False)
        archive = (
            result_pointer.read_text(encoding="utf-8").strip()
            if result_pointer.exists()
            else "No result archive was produced."
        )
        if completed.returncode == 0 and Path(archive).exists():
            from nfs.audio_diagnostic import aggregate_existing_bundle

            try:
                summary = aggregate_existing_bundle(archive)
            except Exception:
                summary = {}
            if summary.get("status") == "driver_timing_failed":
                conclusion = (summary.get("driver_timing_comparison") or {}).get("conclusion")
                comparison_text = {
                    "runaway_timing_reproduced_in_minimal_direct_stream": (
                        "The same timing failure occurred in the minimal direct stream, below the "
                        "application backend."
                    ),
                    "runaway_timing_not_reproduced_in_minimal_direct_stream": (
                        "The minimal direct stream ran at the correct rate; the failure was confined "
                        "to the production-backend test."
                    ),
                    "minimal_direct_stream_unavailable": (
                        "The minimal direct-stream comparison was unavailable."
                    ),
                }.get(conclusion, "The direct-stream comparison is included in the bundle.")
                message = (
                    "The audio driver did not run at the requested real-time rate. "
                    f"{comparison_text}\n\n"
                    f"{summary.get('setup_problem', '')}\n\n"
                    f"Please send this diagnostic bundle:\n\n{archive}\n\nOpen its folder now?"
                )
                title = "Audio driver timing failure"
            elif summary.get("status") == "setup_failed":
                message = (
                    f"The loopback check needs attention:\n\n{summary.get('setup_problem', '')}\n\n"
                    f"A diagnostic bundle was still saved here:\n\n{archive}\n\nOpen its folder now?"
                )
                title = "Check the electrical loopback"
            elif summary.get("status") == "fatal_error":
                message = (
                    "The diagnostic stopped because of an unexpected error. "
                    "Please send the error bundle so it can be inspected:\n\n"
                    f"{archive}\n\nOpen its folder now?"
                )
                title = "Audio diagnostic stopped"
            else:
                message = f"Testing is complete. Please send this file:\n\n{archive}\n\nOpen its folder now?"
                title = "Audio diagnostic complete"
            answer = QMessageBox.question(None, title, message)
            if answer == QMessageBox.StandardButton.Yes:
                subprocess.Popen(["explorer.exe", "/select,", str(Path(archive))])
        else:
            QMessageBox.warning(
                None,
                "Audio diagnostic incomplete",
                f"The diagnostic did not finish normally.\n\n{archive}",
            )
        return 0

    wheel_guard = install_wheel_guard(app)
    try:
        from harmonic_drive_qt import project

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
