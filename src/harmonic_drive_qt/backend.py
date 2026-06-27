"""Backend adapter for the native Qt prototype.

The goal is to keep the Qt UI away from NiceGUI while reusing the existing
scanner, NFS, project, and audio code.
"""

from __future__ import annotations

import ctypes
import queue
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from nfs import NearFieldScannerFactory, ScannerFactory
from nfs.audio import AudioFactory, get_audio_meter_state
from nfs.datatypes import CylindricalPosition
from nfs.logging_config import setup_logging

from .qt_compat import QObject, QRunnable, Signal, Slot


def format_scanner_error(exc: Exception) -> str:
    text = str(exc)
    match = re.search(r"port '([^']+)'", text, re.IGNORECASE)
    if match:
        return (
            f"Scanner connection failed on {match.group(1)}. "
            "Check the port and controller connection."
        )
    if "No GRBL response" in text:
        return "Scanner not responding on the selected port. Check the port and controller type."
    return f"Scanner unavailable: {exc}"


class BackendManager:
    """Owns scanner/audio objects and exposes thread-safe UI operations."""

    def __init__(self, config_file: str = "config.ini") -> None:
        self.config_file = config_file
        self.scanner = None
        self.nfs = None
        self.audio = None
        self.sine_audio = None
        self.load_warning: str | None = None
        self.preview_ir: dict[str, Any] | None = None
        self.project_dir = self._initial_project_dir()
        self._worker_lock = threading.Lock()
        self._measurement_thread: threading.Thread | None = None
        self._progress_callback: Callable[[dict[str, Any]], None] | None = None
        self._audio_queue: queue.Queue = queue.Queue()
        self._audio_thread = threading.Thread(
            target=self._audio_worker,
            daemon=True,
            name="qt-audio-worker",
        )
        self._audio_thread.start()

    def _initial_project_dir(self) -> Path:
        try:
            from harmonic_drive import project

            return project.get_project_dir()
        except Exception:
            return Path.cwd()

    def _audio_worker(self) -> None:
        try:
            if sys.platform == "win32":
                ctypes.windll.ole32.CoInitializeEx(None, 2)
                logger.info("Qt audio worker thread COM initialized.")
        except Exception as exc:
            logger.warning(f"Qt audio worker COM initialization failed: {exc}")

        while True:
            item = self._audio_queue.get()
            if item is None:
                self._audio_queue.task_done()
                break
            func, args, kwargs, done_event, result_holder = item
            try:
                result_holder["result"] = func(*args, **kwargs)
            except Exception as exc:
                result_holder["error"] = exc
                logger.exception("Qt audio worker failed")
            finally:
                done_event.set()
                self._audio_queue.task_done()

        try:
            if sys.platform == "win32":
                ctypes.windll.ole32.CoUninitialize()
        except Exception:
            pass

    def _run_audio(self, func: Callable, *args, **kwargs):
        done_event = threading.Event()
        result_holder: dict[str, Any] = {}
        self._audio_queue.put((func, args, kwargs, done_event, result_holder))
        done_event.wait()
        if "error" in result_holder:
            raise result_holder["error"]
        return result_holder.get("result")

    def load(self, status: Callable[[str], None] | None = None) -> None:
        def set_status(message: str) -> None:
            logger.info(message)
            if status is not None:
                status(message)

        set_status("(Re)loading configuration")
        setup_logging(self.config_file, force=True)
        set_status("Connecting to GRBL")
        self.load_warning = None
        self.scanner = None
        self.nfs = None
        try:
            self.scanner = ScannerFactory.create(self.config_file)
        except Exception as exc:
            self.load_warning = format_scanner_error(exc)
            logger.error("{}: {}", self.load_warning, exc)
            set_status("Scanner unavailable; continuing without motion hardware")

        if self.scanner is not None:
            try:
                set_status("Initializing Near Field Scanner")
                self.nfs = NearFieldScannerFactory.create(self.scanner, self.config_file)
            except Exception as exc:
                self.load_warning = f"Near Field Scanner unavailable: {exc}"
                logger.error(self.load_warning)
                set_status("Near Field Scanner unavailable; continuing")

        if self.project_dir is not None:
            self.set_project_dir(self.project_dir)
        self.audio = None
        self.sine_audio = None

    def set_project_dir(self, path: str | Path) -> None:
        self.project_dir = Path(path).expanduser().resolve()
        self.project_dir.mkdir(parents=True, exist_ok=True)
        if self.nfs is not None and hasattr(self.nfs, "set_project_directory"):
            self.nfs.set_project_directory(self.project_dir)

    def get_position(self):
        if self.scanner is None:
            return None
        return self.scanner.get_position()

    def get_machine_position(self):
        if self.scanner is None:
            return None
        getter = getattr(self.scanner, "get_machine_position", None)
        return getter() if callable(getter) else None

    def get_state(self):
        if self.scanner is None:
            return None
        return self.scanner.get_state()

    def jog(self, method_name: str, value: float) -> None:
        if self.scanner is None:
            raise RuntimeError("Scanner is not loaded")
        getattr(self.scanner, method_name)(value)

    def scanner_command(self, method_name: str) -> None:
        if self.scanner is None:
            raise RuntimeError("Scanner is not loaded")
        getattr(self.scanner, method_name)()

    def set_as_zero(self) -> None:
        if self.scanner is None:
            raise RuntimeError("Scanner is not loaded")
        self.scanner.set_as_zero()

    def set_speaker_center_above_stool(self, height: float = 0.0) -> None:
        if self.scanner is None:
            raise RuntimeError("Scanner is not loaded")
        self.scanner.set_speaker_center_above_stool(height)

    def take_single_measurement(self) -> None:
        if self.nfs is None:
            raise RuntimeError("NFS backend is not loaded")
        self.clear_preview_ir()
        self._run_audio(self.nfs.take_single_measurement)

    def test_sweep(self):
        if hasattr(self.nfs, "test_sweep"):
            result = self._run_audio(self.nfs.test_sweep)
            if isinstance(result, dict):
                self.preview_ir = {**result, "preview_created_at": time.time()}
            return result
        if self.nfs is not None:
            result = self._run_audio(self.nfs.take_single_measurement)
        else:
            if self.audio is None:
                self.audio = AudioFactory.create(self.config_file)
            position = (
                self.scanner.get_position()
                if self.scanner is not None
                else CylindricalPosition(0.0, 0.0, 0.0)
            )
            result = self._run_audio(self.audio.measure_ir, position, "TEST", False)
        if isinstance(result, dict):
            self.preview_ir = {**result, "preview_created_at": time.time()}
        return result

    def get_preview_ir(self) -> dict[str, Any] | None:
        return self.preview_ir

    def clear_preview_ir(self) -> None:
        self.preview_ir = None

    def start_measurement_set(
        self,
        name: str,
        progress_callback: Callable[[dict[str, Any]], None],
        overwrite: bool = False,
    ) -> None:
        if self.nfs is None:
            raise RuntimeError("NFS backend is not loaded")
        if self.is_measurement_set_running():
            return
        self.clear_preview_ir()
        self._progress_callback = progress_callback
        self._measurement_thread = threading.Thread(
            target=self._run_measurement_set,
            args=(name, overwrite),
            daemon=True,
            name="qt-measurement-set",
        )
        self._measurement_thread.start()

    def _run_measurement_set(self, name: str, overwrite: bool) -> None:
        try:
            self._run_audio(
                self.nfs.take_measurement_set,
                name,
                overwrite,
                self._progress_callback,
            )
        except Exception:
            logger.exception("Measurement set failed")

    def pause_measurement_set(self) -> None:
        if self.nfs is not None and hasattr(self.nfs, "pause_measurement_set"):
            self.nfs.pause_measurement_set()

    def resume_measurement_set(self) -> None:
        if self.nfs is not None and hasattr(self.nfs, "resume_measurement_set"):
            self.nfs.resume_measurement_set()

    def stop_measurement_set(self) -> None:
        if self.nfs is not None and hasattr(self.nfs, "stop_measurement_set"):
            self.nfs.stop_measurement_set()

    def is_measurement_set_running(self) -> bool:
        return bool(
            self.nfs is not None
            and hasattr(self.nfs, "is_measurement_set_running")
            and self.nfs.is_measurement_set_running()
        )

    def is_measurement_set_paused(self) -> bool:
        return bool(
            self.nfs is not None
            and hasattr(self.nfs, "is_measurement_set_paused")
            and self.nfs.is_measurement_set_paused()
        )

    def get_measurement_progress(self) -> dict[str, Any]:
        if self.nfs is None or not hasattr(self.nfs, "get_measurement_progress"):
            return {"status": "ready", "current": 0, "total": 0, "eta_seconds": None}
        return self.nfs.get_measurement_progress()

    def get_audio_meter_state(self) -> dict[str, Any]:
        return get_audio_meter_state()

    def play_sine(self, frequency: float, level: float, duration: Optional[float]) -> None:
        if self.nfs is not None and hasattr(self.nfs, "play_sine"):
            return self._run_audio(self.nfs.play_sine, frequency, level, duration)
        if self.sine_audio is None:
            self.sine_audio = AudioFactory.create(self.config_file)
        return self._run_audio(self.sine_audio.play_sine, frequency, level, duration)

    def stop_sine(self) -> None:
        if self.nfs is not None and hasattr(self.nfs, "stop_sine"):
            self.nfs.stop_sine()
        if self.audio is not None and hasattr(self.audio, "stop_sine"):
            self.audio.stop_sine()
        if self.sine_audio is not None and hasattr(self.sine_audio, "stop_sine"):
            self.sine_audio.stop_sine()

    def shutdown(self) -> None:
        try:
            self.stop_sine()
        except Exception:
            logger.exception("Could not stop sine audio")
        if self.nfs is not None and hasattr(self.nfs, "shutdown"):
            self.nfs.shutdown()
        self._audio_queue.put(None)


class WorkerSignals(QObject):
    finished = Signal()
    failed = Signal(str)


class Worker(QRunnable):
    """Run blocking backend calls off the Qt UI thread."""

    def __init__(self, func: Callable, *args, **kwargs) -> None:
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.func(*self.args, **self.kwargs)
        except Exception as exc:
            logger.exception("Qt worker failed")
            self.signals.failed.emit(str(exc))
        else:
            self.signals.finished.emit()


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    seconds = max(0, int(round(seconds)))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {sec:02d}s"
    return f"{sec}s"
