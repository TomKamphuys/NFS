import asyncio
import configparser
import ctypes
import os
import queue
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from nicegui import app, context, run, ui

from harmonic_drive.config_editor import (
    check_audio_device_ids_on_startup,
    set_file_measurement_points_filename,
)
from harmonic_drive import project, reconnect_debug
from nfs import NearFieldScannerFactory, ScannerFactory
from nfs.audio import AudioFactory
from nfs.datatypes import CylindricalPosition
from nfs.logging_config import setup_logging

if TYPE_CHECKING:
    from nfs.nfs import NearFieldScanner
    from nfs.scanner import Scanner


scanner_app = None
log_handler = None
log_handler_sink_id = None
is_playing = False
sine_audio = None
sine_target = None
play_button = None
play_button_icon = None
level_input = None
freq_input = None
dur_input = None
measurement_start_button = None
measurement_stop_button = None
measurement_progress_panel = None
measurement_progress_fill = None
measurement_progress_percent_label = None
measurement_progress_detail_label = None
pos_r = None
pos_t = None
pos_z = None
pos_state = None
mcs_pos_r = None
mcs_pos_t = None
mcs_pos_z = None
mcs_pos_state = None
position_display_titles = []
mcs_position_display = None
wcs_position_display = None
home_button = None
zero_button = None

on_config_loaded = None
measurement_set_title_provider = None
project_root_provider = None
session_folder_guard = None
measurement_progress_state = {
    "eta_seconds": None,
    "current": 0,
    "total": 0,
    "status": "Ready",
}

NOTIFY_ARG_MAP = {
    "close_button": "closeBtn",
    "multi_line": "multiLine",
}


def _current_client():
    try:
        return context.client
    except RuntimeError:
        return None


def _client_can_receive_notifications(client) -> bool:
    try:
        has_socket_connection = getattr(client, "has_socket_connection", False)
        if callable(has_socket_connection):
            has_socket_connection = has_socket_connection()
        return (
            client is not None
            and not getattr(client, "is_deleted", False)
            and not getattr(client, "_deleted", False)
            and bool(has_socket_connection)
        )
    except RuntimeError:
        return False


def _safe_notify(client, message: Any, **kwargs) -> None:
    if not _client_can_receive_notifications(client):
        logger.debug("UI notification skipped because the browser client is no longer available: {}", message)
        return

    options = {"message": str(message)}
    for key, value in kwargs.items():
        options[NOTIFY_ARG_MAP.get(key, key)] = value
    try:
        client.outbox.enqueue_message("notify", options, client.id)
    except RuntimeError as exc:
        logger.debug("UI notification skipped because the browser client became unavailable: {}; {}", message, exc)


def _is_deleted(element) -> bool:
    return element is None or bool(
        getattr(element, "is_deleted", False)
        or getattr(element, "_deleted", False)
    )


def _safe_enable(element) -> None:
    if not _is_deleted(element):
        try:
            element.enable()
        except RuntimeError as exc:
            logger.info("Skipped enabling deleted UI element: {}", exc)


def _safe_disable(element) -> None:
    if not _is_deleted(element):
        try:
            element.disable()
        except RuntimeError as exc:
            logger.info("Skipped disabling deleted UI element: {}", exc)


def _safe_update_element(update) -> None:
    try:
        update()
    except RuntimeError as exc:
        if "deleted" in str(exc).lower() or "slot" in str(exc).lower():
            logger.info("Skipped update for unavailable UI element: {}", exc)
            return
        raise


def set_on_config_loaded(callback):
    global on_config_loaded
    on_config_loaded = callback


def set_measurement_set_context(title_provider, root_provider, guard=None) -> None:
    global measurement_set_title_provider, project_root_provider, session_folder_guard
    measurement_set_title_provider = title_provider
    project_root_provider = root_provider
    session_folder_guard = guard


async def _ensure_session_folder_selected() -> bool:
    if session_folder_guard is None:
        return True
    result = session_folder_guard()
    if asyncio.iscoroutine(result):
        result = await result
    return bool(result)


async def ensure_session_folder_selected() -> bool:
    return await _ensure_session_folder_selected()


def register_sine_controls(level, frequency, duration, button, button_icon=None) -> None:
    global level_input, freq_input, dur_input, play_button, play_button_icon
    level_input = level
    freq_input = frequency
    dur_input = duration
    play_button = button
    play_button_icon = button_icon


def _set_sine_button_icon(icon_name: str) -> None:
    if not _is_deleted(play_button_icon):
        _safe_update_element(lambda: play_button_icon.set_name(icon_name))
        _safe_update_element(play_button_icon.update)
        return
    if not _is_deleted(play_button):
        try:
            play_button.set_icon(icon_name)
            play_button._props['icon'] = icon_name
            _safe_update_element(play_button.update)
        except AttributeError:
            play_button.props(f"icon={icon_name}")
            _safe_update_element(play_button.update)


def _get_audio_target(purpose: str):
    global sine_audio
    nfs = get_nfs()
    if nfs is not None:
        return nfs
    if scanner_app is None:
        raise RuntimeError("App is not initialized yet")
    if sine_audio is None:
        logger.info(
            f"NearFieldScanner is unavailable; creating audio-only {purpose} backend"
        )
        sine_audio = AudioFactory.create(scanner_app.config_file)
    return sine_audio


def _get_sine_audio_target():
    return _get_audio_target("sine")


def _get_test_sweep_call():
    target = _get_audio_target("test sweep")
    if hasattr(target, "test_sweep"):
        return target.test_sweep, ()
    return target.measure_ir, (CylindricalPosition(0.0, 0.0, 0.0), "TEST", False)


def _format_scanner_error(exc: Exception) -> str:
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


def _log_built_object_tree(scanner, nfs, config_file: str) -> None:
    """Log a readable summary of the freshly built object tree."""

    def _public_attrs(obj) -> list:
        out = []
        for name in sorted(vars(obj).keys()) if hasattr(obj, "__dict__") else []:
            try:
                value = getattr(obj, name)
            except Exception:
                continue
            if callable(value):
                continue
            display_name = name[1:] if name.startswith("_") else name
            if display_name.startswith("_") or display_name == "points":
                continue
            cls_name = type(value).__name__
            if cls_name in {
                "Scanner",
                "NearFieldScanner",
                "CylindricalMeasurementMotionManager",
                "SphericalMeasurementMotionManager",
            } or "MeasurementPoints" in cls_name or "GrblController" in cls_name \
                    or "Audio" in cls_name and not isinstance(
                        value, (str, int, float, bool)
                    ):
                continue
            out.append((display_name, value))
        return out

    def _fmt_value(value) -> str:
        if isinstance(value, float):
            return f"{value:g}"
        if isinstance(value, (list, tuple, set, frozenset)):
            items = list(value)
            if len(items) <= 6:
                inner = ", ".join(_fmt_value(item) for item in items)
                bracket = ("[", "]") if isinstance(value, list) else \
                          ("(", ")") if isinstance(value, tuple) else ("{", "}")
                return f"{bracket[0]}{inner}{bracket[1]}"
            lines = [_fmt_value(item) for item in items]
            joined = ",\n          ".join(lines)
            bracket = ("[", "]") if isinstance(value, list) else \
                      ("(", ")") if isinstance(value, tuple) else ("{", "}")
            return f"{bracket[0]}\n          {joined}\n        {bracket[1]}"
        if isinstance(value, dict):
            if len(value) <= 6:
                inner = ", ".join(
                    f"{key!r}: {_fmt_value(val)}" for key, val in value.items()
                )
                return "{" + inner + "}"
            lines = [
                f"{key!r}: {_fmt_value(val)}" for key, val in value.items()
            ]
            joined = ",\n          ".join(lines)
            return "{\n          " + joined + "\n        }"
        return repr(value)

    lines = ["Configuration reloaded - built object tree:"]
    lines.append(f"  config_file: {config_file}")

    def _emit(obj, label: str, indent: str) -> None:
        if obj is None:
            lines.append(f"{indent}{label}: <none>")
            return
        lines.append(f"{indent}{label}: {type(obj).__name__}")
        for name, value in _public_attrs(obj):
            lines.append(f"{indent}  - {name} = {_fmt_value(value)}")

    _emit(scanner, "Scanner", "  ")
    _emit(getattr(scanner, "_grbl_controller", None), "GrblController", "    ")
    _emit(nfs, "NearFieldScanner", "  ")
    _emit(getattr(nfs, "_audio", None), "Audio", "    ")
    mm = getattr(nfs, "_measurement_motion_manager", None)
    _emit(mm, "MotionManager", "    ")
    mp = getattr(mm, "_measurement_points", None) if mm is not None else None
    _emit(mp, "MeasurementPoints", "      ")
    try:
        total = mp.total_points() if mp is not None else None
    except Exception:
        total = None
    if total is not None:
        lines.append(f"        - total_points = {total}")

    logger.info("\n".join(lines))


class ScannerApp:
    def __init__(self, config_file: str):
        self.config_file = config_file
        self.scanner = None
        self.nfs = None
        self.greyable_buttons = []
        self.home_state = {'ok': False}
        self.log_handler = None
        self._load_lock = threading.Lock()
        self._is_loaded = False
        self.load_warning = None

    def load_config(self, status_callback=None):
        with self._load_lock:
            if self._is_loaded:
                attach_gui_log_handler()
                return

            def update_status(msg):
                logger.info(msg)
                if status_callback:
                    status_callback(msg)

            update_status("(Re)loading configuration")
            setup_logging(self.config_file, project_name="HarmonicDrive")
            attach_gui_log_handler()
            update_status("Connecting to GRBL")
            self.load_warning = None
            self.scanner = None
            self.nfs = None
            try:
                self.scanner = ScannerFactory.create(self.config_file)
            except Exception as exc:
                self.load_warning = _format_scanner_error(exc)
                logger.error(f"{self.load_warning}: {exc}")
                update_status("Scanner unavailable; continuing without motion hardware")

            if self.scanner is not None:
                try:
                    update_status("Initializing Near Field Scanner & reading points")
                    self.nfs = NearFieldScannerFactory.create(self.scanner, self.config_file)
                except Exception as exc:
                    self.load_warning = f"Near Field Scanner unavailable: {exc}"
                    logger.error(self.load_warning)
                    update_status("Near Field Scanner unavailable; continuing")

                self.scanner.set_on_state_update_callback(update_scanner_position)

            _log_built_object_tree(self.scanner, self.nfs, self.config_file)
            self._is_loaded = True

    def reload_config_ui(self, notify: bool = True):
        try:
            with self._load_lock:
                self._is_loaded = False
            self.load_config()
            apply_project_directory_to_nfs()
            if on_config_loaded:
                on_config_loaded()
            apply_position_display_config()
            if notify:
                if self.load_warning:
                    ui.notify(self.load_warning, type='warning')
                else:
                    ui.notify("Configuration reloaded successfully", type='positive')
        except Exception as exc:
            logger.error(f"Failed to reload configuration: {exc}")
            ui.notify(f"Reload failed: {exc}", type='negative')


def get_scanner() -> "Scanner":
    if scanner_app is None:
        return None
    return scanner_app.scanner


def get_nfs() -> "NearFieldScanner":
    if scanner_app is None:
        return None
    return scanner_app.nfs


def apply_project_directory_to_nfs() -> None:
    project.ensure_output_dirs()
    nfs = get_nfs() if scanner_app else None
    if nfs is not None and hasattr(nfs, "set_project_directory"):
        nfs.set_project_directory(project.get_project_dir())


def measurement_outputs_exist(measurement_set_dir: Path) -> bool:
    measurement_dir = measurement_set_dir / "measurement_set"
    if not measurement_dir.exists():
        return False
    for path in measurement_dir.rglob("*"):
        if path.is_dir():
            continue
        return True
    return False


def loaded_grid_file_exists(measurement_set_dir: Path) -> bool:
    grid_vars = project.get_project_data().get("grid_vars")
    if isinstance(grid_vars, dict):
        filename = grid_vars.get("output_filename")
        if filename and (measurement_set_dir / str(filename)).exists():
            return True
    return False


def use_generated_grid_file(filename: str, grid_vars=None):
    try:
        config_filename = str((project.get_project_dir() / filename).resolve())
        section = set_file_measurement_points_filename(
            scanner_app.config_file,
            config_filename,
            scanner_app.reload_config_ui,
        )
        updates = {"output_filename": filename}
        if isinstance(grid_vars, dict):
            updates.update(grid_vars)
        project.update_grid_vars(updates)
        ui.notify(
            f"Using generated grid '{filename}' via [{section}]",
            type='positive',
        )
    except Exception as exc:
        logger.error(f"Failed to use generated grid file: {exc}")
        ui.notify(f"Generated grid saved, but config update failed: {exc}", type='negative')


class LogBuffer:
    def __init__(self, max_lines=2000):
        self.buffer = queue.Queue()
        self.max_lines = max_lines

    def write(self, message):
        self.buffer.put(message.strip())


def attach_gui_log_handler() -> None:
    global log_handler_sink_id
    if log_handler is None or log_handler_sink_id is not None:
        return
    log_handler_sink_id = logger.add(
        log_handler.write,
        level="TRACE",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
            "{name}:{function}:{line} - {message}"
        ),
    )


def log_button_click(label: str, handler):
    """Wrap a NiceGUI handler so clicks are logged consistently."""

    async def _wrapped(*args, **kwargs):
        logger.info("UI click: {}", label)
        result = handler(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    return _wrapped


audio_queue = queue.Queue()


def audio_worker():
    """Run measurement/audio work on a dedicated thread for picky drivers."""
    try:
        if sys.platform == 'win32':
            ctypes.windll.ole32.CoInitializeEx(None, 2)
            logger.info("Audio worker thread COM initialized.")
    except Exception as exc:
        logger.warning(f"COM initialization failed: {exc}")

    while True:
        item = audio_queue.get()
        if item is None:
            break

        if len(item) == 5:
            func, args, done_event, loop, result_holder = item
        else:
            func, args, done_event, loop = item
            result_holder = None
        try:
            reconnect_debug.log_worker_event("start", func, f"queue_size={audio_queue.qsize()}")
            result = func(*args)
            if result_holder is not None:
                result_holder['result'] = result
            reconnect_debug.log_worker_event("finish", func, f"queue_size={audio_queue.qsize()}")
        except StopIteration:
            logger.info("Measurement sequence completed: all points processed.")
            reconnect_debug.log_worker_event("stop-iteration", func)
        except Exception as exc:
            if "No more points" in str(exc):
                logger.info("Measurement sequence completed: all points processed.")
                reconnect_debug.log_worker_event("no-more-points", func)
            else:
                logger.error(f"Audio worker failed: {exc}")
                reconnect_debug.log_worker_event("error", func, repr(exc))
        finally:
            try:
                loop.call_soon_threadsafe(done_event.set)
            except RuntimeError as exc:
                logger.warning(f"Audio worker could not notify browser task completion: {exc}")
            audio_queue.task_done()

    try:
        if sys.platform == 'win32':
            ctypes.windll.ole32.CoUninitialize()
    except Exception:
        pass


worker_thread = threading.Thread(target=audio_worker, daemon=True)
worker_thread.start()


def show_shutdown_screen() -> None:
    try:
        ui.run_javascript("""
        (() => {
          const existing = document.getElementById('hals-shutdown-overlay');
          if (existing) return;
          const appRoot = document.getElementById('q-app') || document.body;
          appRoot.style.filter = 'grayscale(1)';
          appRoot.style.opacity = '0.32';
          appRoot.style.pointerEvents = 'none';

          const overlay = document.createElement('div');
          overlay.id = 'hals-shutdown-overlay';
          overlay.style.cssText = [
            'position: fixed',
            'inset: 0',
            'z-index: 2147483647',
            'display: flex',
            'align-items: center',
            'justify-content: center',
            'background: rgba(8, 12, 18, 0.72)',
            'backdrop-filter: blur(2px)',
            'font-family: system-ui, sans-serif',
            'color: #f8fafc',
            'text-align: center',
          ].join(';');
          overlay.innerHTML = `
            <div>
              <div style="font-size: clamp(48px, 9vw, 118px); font-weight: 900; letter-spacing: 0.08em;">
                SHUTDOWN
              </div>
              <div style="margin-top: 14px; font-size: 18px; color: #cbd5e1;">
                HarmonicDrive server has been stopped.
              </div>
            </div>
          `;
          document.body.appendChild(overlay);
        })();
        """)
    except Exception:
        pass


async def shutdown_from_ui():
    show_shutdown_screen()
    await asyncio.sleep(0.7)
    await run.io_bound(stop_nfs)


def stop_nfs():
    logger.info('Stopping NFS and shutting down...')
    global is_playing, sine_audio, sine_target
    try:
        show_shutdown_screen()
        audio_queue.put(None)
        if sine_target is not None:
            try:
                sine_target.stop_sine()
            except Exception as exc:
                logger.warning(f"Error stopping sine target during shutdown: {exc}")
        elif sine_audio is not None:
            try:
                sine_audio.stop_sine()
            except Exception as exc:
                logger.warning(f"Error stopping audio-only sine during shutdown: {exc}")
        if scanner_app and scanner_app.nfs:
            scanner_app.nfs.shutdown()
        is_playing = False
        sine_target = None
        sine_audio = None
        _set_sine_button_icon('play_arrow')
        time.sleep(0.5)
        app.shutdown()
        os._exit(0)
    except Exception as exc:
        logger.error(f"Error during shutdown: {exc}")
        os._exit(1)


def hold_scanner():
    try:
        get_scanner().hold()
    except Exception as exc:
        logger.error(f"Error during HOLD: {exc}")


app.on_shutdown(stop_nfs)


def _update_measurement_buttons() -> None:
    nfs = get_nfs()
    running = bool(
        nfs is not None
        and hasattr(nfs, "is_measurement_set_running")
        and nfs.is_measurement_set_running()
    )
    paused = bool(
        nfs is not None
        and hasattr(nfs, "is_measurement_set_paused")
        and nfs.is_measurement_set_paused()
    )
    if not _is_deleted(measurement_start_button):
        if paused:
            _set_measurement_primary_button("Resume", "play_arrow", "primary")
        elif running:
            _set_measurement_primary_button("Pause", "pause", "warning")
        else:
            _set_measurement_primary_button(
                "Start Measurements",
                "play_arrow",
                "primary",
            )
        _safe_enable(measurement_start_button)
    if not _is_deleted(measurement_stop_button):
        if running:
            _safe_enable(measurement_stop_button)
        else:
            _safe_disable(measurement_stop_button)


def _set_measurement_primary_button(text: str, icon: str, color: str) -> None:
    if _is_deleted(measurement_start_button):
        return
    _safe_update_element(lambda: measurement_start_button.set_text(text))
    _safe_update_element(lambda: measurement_start_button.props(f"icon={icon} color={color}"))


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "Calculating..."
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _reset_measurement_progress() -> None:
    measurement_progress_state["eta_seconds"] = None
    measurement_progress_state["current"] = 0
    measurement_progress_state["total"] = 0
    measurement_progress_state["status"] = "Ready"
    _redraw_measurement_progress(sync_backend=False)


def _handle_measurement_progress(event: dict[str, Any]) -> None:
    reconnect_debug.record_progress_update(event, source="callback")
    _store_measurement_progress(event)
    _redraw_measurement_progress(sync_backend=False)


def _store_measurement_progress(event: dict[str, Any]) -> None:
    status = str(event.get("status", ""))
    current = int(event.get("current") or 0)
    total = int(event.get("total") or 0)
    eta_seconds = event.get("eta_seconds")
    measurement_progress_state["eta_seconds"] = (
        float(eta_seconds) if eta_seconds is not None else None
    )

    if status == "finished" and total > 0 and current >= total:
        measurement_progress_state["eta_seconds"] = 0

    if status == "ready":
        label_status = "Ready"
    elif status == "finished" and total > 0 and current >= total:
        label_status = "Complete"
    elif status == "finished" and (total <= 0 or current < total):
        label_status = "Stopped"
    else:
        label_status = "Running"
    measurement_progress_state["current"] = current
    measurement_progress_state["total"] = total
    measurement_progress_state["status"] = label_status


def _sync_measurement_progress_from_backend() -> None:
    nfs = get_nfs()
    if nfs is not None and hasattr(nfs, "get_measurement_progress"):
        try:
            _store_measurement_progress(nfs.get_measurement_progress())
            return
        except Exception as exc:
            logger.debug(f"Could not refresh measurement progress from backend: {exc}")

    if (
        measurement_progress_state.get("status") == "Running"
        and nfs is not None
        and hasattr(nfs, "is_measurement_set_running")
        and not nfs.is_measurement_set_running()
    ):
        total = int(measurement_progress_state.get("total") or 0)
        measurement_progress_state["current"] = total
        measurement_progress_state["eta_seconds"] = 0
        measurement_progress_state["status"] = "Complete" if total > 0 else "Ready"


def _redraw_measurement_progress(sync_backend: bool = True) -> None:
    reconnect_debug.record_progress_update(
        {
            "current": measurement_progress_state.get("current"),
            "total": measurement_progress_state.get("total"),
            "status": measurement_progress_state.get("status"),
        },
        source="redraw",
    )
    if sync_backend:
        _sync_measurement_progress_from_backend()
    _update_measurement_progress_display(
        int(measurement_progress_state.get("current") or 0),
        int(measurement_progress_state.get("total") or 0),
        str(measurement_progress_state.get("status") or "Ready"),
    )


def refresh_measurement_progress() -> None:
    _redraw_measurement_progress()


def _update_measurement_progress_display(current: int, total: int, status: str) -> None:
    if _is_deleted(measurement_progress_panel):
        return

    percent = (current / total * 100) if total > 0 else 0.0
    percent = max(0.0, min(100.0, percent))
    eta_text = _format_duration(measurement_progress_state.get("eta_seconds"))
    detail = (
        f"{status} - {current} of {total} points - ETA {eta_text}"
        if total > 0 else
        f"{status} - waiting for measurement points"
    )

    _safe_update_element(lambda: measurement_progress_panel.set_visibility(True))
    if not _is_deleted(measurement_progress_fill):
        _safe_update_element(lambda: measurement_progress_fill.style(
            f"width: {percent:.1f}%; transition: width 0.25s ease;"
        ))
    if not _is_deleted(measurement_progress_percent_label):
        _safe_update_element(lambda: measurement_progress_percent_label.set_text(f"{percent:.1f}%"))
    if not _is_deleted(measurement_progress_detail_label):
        _safe_update_element(lambda: measurement_progress_detail_label.set_text(detail))


def _build_measurement_progress_panel() -> None:
    global measurement_progress_panel, measurement_progress_fill
    global measurement_progress_percent_label, measurement_progress_detail_label

    with ui.column().classes(
        "w-[536px] max-w-full gap-1 mt-1 rounded border border-gray-300 bg-white p-2"
    ) as measurement_progress_panel:
        with ui.element("div").classes(
            "relative h-7 w-full overflow-hidden rounded bg-gray-200"
        ):
            measurement_progress_fill = ui.element("div").classes(
                "absolute left-0 top-0 h-full bg-blue-600"
            ).style("width: 0%; transition: width 0.25s ease;")
            measurement_progress_percent_label = ui.label("0.0%").classes(
                "absolute inset-0 flex items-center justify-center text-xs font-bold text-white"
            ).style("text-shadow: 0 1px 2px rgba(0, 0, 0, 0.7);")
        measurement_progress_detail_label = ui.label(
            "Ready - waiting for measurement points"
        ).classes("w-full truncate text-xs font-semibold text-gray-700")
    _redraw_measurement_progress()


def pause_measurement_set():
    nfs = get_nfs()
    if nfs is None or not hasattr(nfs, "pause_measurement_set"):
        ui.notify("Measurement set is not available", type="warning")
        return
    nfs.pause_measurement_set()
    ui.notify("Measurement set will pause after the current operation")
    _update_measurement_buttons()


def stop_measurement_set():
    nfs = get_nfs()
    if nfs is None or not hasattr(nfs, "stop_measurement_set"):
        ui.notify("Measurement set is not available", type="warning")
        return
    nfs.stop_measurement_set()
    ui.notify("Measurement set will stop after the current operation")
    _update_measurement_buttons()


def rehome():
    get_scanner().softreset()
    time.sleep(1)
    get_scanner().clear_alarm()
    time.sleep(1)
    get_scanner().home()


async def toggle_measurement_set():
    client = _current_client()
    reconnect_debug.set_measurement_phase("measurement-set-handler")
    nfs = get_nfs()
    if (
        nfs is not None
        and hasattr(nfs, "is_measurement_set_paused")
        and nfs.is_measurement_set_paused()
    ):
        nfs.resume_measurement_set()
        _safe_notify(client, "Measurement set resumed")
        _update_measurement_buttons()
        return
    if (
        nfs is not None
        and hasattr(nfs, "is_measurement_set_running")
        and nfs.is_measurement_set_running()
    ):
        nfs.pause_measurement_set()
        _safe_notify(client, "Measurement set will pause after the current operation")
        _update_measurement_buttons()
        return

    if not await _ensure_session_folder_selected():
        return

    title = (
        measurement_set_title_provider()
        if measurement_set_title_provider is not None
        else project.get_project_name()
    )
    title = str(title or "").strip() or project.DEFAULT_PROJECT_NAME
    measurement_set_name = project.sanitize_project_name(title)
    project_root = (
        project_root_provider()
        if project_root_provider is not None
        else str(project.get_default_project_root(scanner_app.config_file))
    )
    target_dir = Path(project_root).expanduser().resolve()
    overwrite = False

    if not loaded_grid_file_exists(target_dir):
        _safe_notify(client, "No grid file loaded. Please generate one first.", type="warning")
        return

    if measurement_outputs_exist(target_dir):
        decision = asyncio.Future()
        with ui.dialog() as dialog, ui.card().classes("w-[420px] max-w-full"):
            ui.label("Measurement output already exists").classes("text-lg font-bold")
            ui.label(
                "This measurement folder already contains measurement output. "
                "Choose a different folder, overwrite it, or cancel."
            ).classes("text-sm text-gray-700")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=lambda: (decision.set_result("cancel"), dialog.close())).props("flat")
                ui.button("Overwrite", on_click=lambda: (decision.set_result("overwrite"), dialog.close())).props("color=negative")
        dialog.open()
        choice = await decision
        if choice == "cancel":
            _safe_notify(client, "Measurement cancelled", type="warning")
            return
        overwrite = True
        measurement_dir = target_dir / "measurement_set"
        if measurement_dir.exists():
            shutil.rmtree(measurement_dir)
        for filename in ("measurement_positions.csv",):
            path = target_dir / filename
            if path.exists():
                path.unlink()
        log_file = target_dir / "logs" / "Scanner.log"
        if log_file.exists():
            log_file.unlink()

    project.save_project_to(target_dir, title, scanner_app.config_file)
    apply_project_directory_to_nfs()
    from harmonic_drive import live_capture
    live_capture.reset_live_capture_session()

    _safe_notify(client, 'Measurement started')
    reconnect_debug.set_measurement_phase("measurement-set-running")
    for button in scanner_app.greyable_buttons:
        _safe_disable(button)
    _update_measurement_buttons()
    completed = False
    try:
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        _reset_measurement_progress()

        audio_queue.put((
            nfs.take_measurement_set,
            (measurement_set_name, overwrite),
            done,
            loop,
        ))
        if not _is_deleted(measurement_stop_button):
            _safe_enable(measurement_stop_button)
        if not _is_deleted(measurement_start_button):
            _set_measurement_primary_button("Pause", "pause", "warning")
            _safe_enable(measurement_start_button)
        await done.wait()
        completed = True
    except Exception as exc:
        logger.error(f"Measurement task failed: {exc}")
        reconnect_debug.set_measurement_phase("measurement-set-error")
        _safe_notify(client, f"Error: {exc}", type='negative')
    finally:
        _redraw_measurement_progress()
        still_running = bool(
            nfs is not None
            and hasattr(nfs, "is_measurement_set_running")
            and nfs.is_measurement_set_running()
        )
        if completed:
            _safe_notify(client, 'Measurement finished')
        reconnect_debug.set_measurement_phase("measurement-set-finished")
        if not still_running:
            for button in scanner_app.greyable_buttons:
                _safe_enable(button)
        _update_measurement_buttons()


async def async_single_measurement_task():
    client = _current_client()
    reconnect_debug.set_measurement_phase("single-measurement-handler")
    if not await _ensure_session_folder_selected():
        return

    _safe_notify(client, 'Single measurement started')
    reconnect_debug.set_measurement_phase("single-measurement-running")
    for button in scanner_app.greyable_buttons:
        _safe_disable(button)
    try:
        title = (
            measurement_set_title_provider()
            if measurement_set_title_provider is not None
            else project.get_project_name()
        )
        title = str(title or "").strip() or project.DEFAULT_PROJECT_NAME
        project_root = (
            project_root_provider()
            if project_root_provider is not None
            else str(
                project.get_default_project_root(scanner_app.config_file)
                / project.sanitize_project_name(title)
            )
        )
        target_dir = Path(project_root).expanduser().resolve()
        project.save_project_to(target_dir, title, scanner_app.config_file)
        apply_project_directory_to_nfs()

        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        audio_queue.put((get_nfs().take_single_measurement, (), done, loop))
        await done.wait()
    except Exception as exc:
        logger.error(f"Single measurement failed: {exc}")
        reconnect_debug.set_measurement_phase("single-measurement-error")
        _safe_notify(client, f"Error: {exc}", type='negative')
    finally:
        _safe_notify(client, 'Single measurement finished')
        reconnect_debug.set_measurement_phase("single-measurement-finished")
        for button in scanner_app.greyable_buttons:
            _safe_enable(button)


async def async_test_sweep_task():
    client = _current_client()
    reconnect_debug.set_measurement_phase("test-sweep-running")
    _safe_notify(client, 'Test sweep started')
    try:
        sweep_func, sweep_args = _get_test_sweep_call()
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        result_holder = {}
        audio_queue.put((sweep_func, sweep_args, done, loop, result_holder))
        await done.wait()
        result = result_holder.get('result')
        if result is not None:
            from harmonic_drive import live_capture
            try:
                live_capture.set_preview_ir(result)
            except RuntimeError as exc:
                if "slot" in str(exc).lower() or "deleted" in str(exc).lower():
                    logger.info("Skipped test sweep preview refresh because the browser context is no longer available: {}", exc)
                else:
                    raise
    except Exception as exc:
        logger.error(f"Test sweep failed: {exc}")
        reconnect_debug.set_measurement_phase("test-sweep-error")
        _safe_notify(client, f"Error: {exc}", type='negative')
    finally:
        _safe_notify(client, 'Test sweep finished')
        reconnect_debug.set_measurement_phase("test-sweep-finished")


async def async_play_sine_task():
    global is_playing, sine_target
    client = _current_client()
    reconnect_debug.set_measurement_phase("sine-handler")
    if is_playing:
        if sine_target is not None:
            sine_target.stop_sine()
        is_playing = False
        sine_target = None
        _set_sine_button_icon('play_arrow')
        return

    level = level_input.value if level_input.value is not None else -20.0
    freq = freq_input.value if freq_input.value is not None else 1000.0
    dur = float(dur_input.value) if dur_input.value is not None else None

    try:
        sine_target = _get_sine_audio_target()
    except Exception as exc:
        logger.error(f"Could not initialize sine audio backend: {exc}")
        _safe_notify(client, f"Audio unavailable: {exc}", type='negative')
        sine_target = None
        return

    is_playing = True
    reconnect_debug.set_measurement_phase("sine-running")
    _set_sine_button_icon('stop')

    try:
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        audio_queue.put((sine_target.play_sine, (freq, level, dur), done, loop))
        await done.wait()
    except Exception as exc:
        logger.error(f"Play sine failed: {exc}")
        reconnect_debug.set_measurement_phase("sine-error")
        _safe_notify(client, f"Error: {exc}", type='negative')
        sine_target = None
    finally:
        if dur is not None:
            is_playing = False
            sine_target = None
            reconnect_debug.set_measurement_phase("sine-finished")
            _set_sine_button_icon('play_arrow')


async def safe_move(func, *args):
    for button in scanner_app.greyable_buttons:
        _safe_disable(button)
    try:
        await run.io_bound(func, *args)
    finally:
        for button in scanner_app.greyable_buttons:
            _safe_enable(button)


async def zero_nfs_then_apply_height_offset(height_value: float):
    await run.io_bound(get_scanner().set_as_zero)
    if not height_value:
        return
    await run.io_bound(
        get_scanner().set_speaker_center_above_stool,
        height_value,
    )


def _scanner_has_alarm() -> bool:
    try:
        state = get_scanner().get_state()
        if state is None:
            return False
        name = getattr(state, 'name', str(state))
        return str(name).upper() == 'ALARM'
    except Exception:
        return False


def _set_home_button_color(color: str) -> None:
    try:
        home_button.props(f'color={color}')
    except Exception:
        pass


def _get_raw_state_string():
    try:
        state = get_scanner().get_state()
        if state is None:
            return None
        if hasattr(state, 'name'):
            return state.name
        return str(state).split('.')[-1]
    except Exception:
        return None


def _format_position_value(pos, axis: str) -> str:
    if pos is None:
        return '   -   '
    if axis == 'r':
        return f'{pos.r():7.2f}'
    if axis == 't':
        return f'{pos.t():7.2f}'
    return f'{pos.z():7.2f}'


def _format_state_value(state) -> str:
    if state is None:
        raw_state = _get_raw_state_string()
    else:
        raw_state = state.name if hasattr(state, 'name') \
            else str(state).split('.')[-1]
    return f'{raw_state:^8}' if raw_state else '   -   '


def _set_position_labels(targets, pos, state=None):
    r_label, t_label, z_label, state_label = targets
    for label in targets:
        if label is None:
            return
    r_label.set_text(_format_position_value(pos, 'r'))
    t_label.set_text(_format_position_value(pos, 't'))
    z_label.set_text(_format_position_value(pos, 'z'))
    state_label.set_text(_format_state_value(state))


def update_scanner_position(pos=None, state=None, machine_pos=None):
    reconnect_debug.record_scanner_update(state)

    def do_update():
        nonlocal pos, state, machine_pos
        if pos is None:
            pos = get_scanner().get_position()
        if machine_pos is None:
            machine_getter = getattr(get_scanner(), "get_machine_position", None)
            if machine_getter is not None:
                machine_pos = machine_getter()

        _set_position_labels((pos_r, pos_t, pos_z, pos_state), pos, state)
        _set_position_labels(
            (mcs_pos_r, mcs_pos_t, mcs_pos_z, mcs_pos_state),
            machine_pos,
            state,
        )

        if _scanner_has_alarm():
            pos_state.classes(remove='text-[#7eff00]').classes(
                add='text-red-600 alarm_blink'
            )
            if mcs_pos_state is not None:
                mcs_pos_state.classes(remove='text-orange-400').classes(
                    add='text-red-600 alarm_blink'
            )
            scanner_app.home_state['ok'] = False
            _set_home_button_color('orange')
        else:
            pos_state.classes(remove='text-red-600 alarm_blink').classes(
                add='text-[#7eff00]'
            )
            if mcs_pos_state is not None:
                mcs_pos_state.classes(remove='text-red-600 alarm_blink').classes(
                    add='text-orange-400'
                )
            _set_home_button_color(
                'green' if scanner_app.home_state['ok'] else 'orange'
            )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(do_update)
        else:
            do_update()
    except RuntimeError:
        do_update()


def add_jog_row(axis: str, left_label: str, right_label: str, unit: str,
                left_moves: list, right_moves: list):
    async def _execute_move(method_name: str, value: float):
        scanner = get_scanner()
        if not scanner:
            ui.notify("Scanner not initialized", type='warning')
            return
        method = getattr(scanner, method_name)
        await safe_move(method, value)

    with ui.column().classes('w-full'):
        with ui.element('div').classes('jog-grid'):
            ui.label('')
            ui.label(left_label).classes('jog-hdr jog-hdr-left')
            ui.label('HOLD').classes('jog-hdr jog-hdr-stop')
            ui.label(right_label).classes('jog-hdr jog-hdr-right')
        with ui.element('div').classes('jog-grid'):
            ui.html(
                f'<div class="jog-axis">{axis}:'
                f'<div class="jog-unit">{unit}</div></div>'
            )
            for value, method_name in left_moves:
                button = ui.button(
                    f'{value}',
                    on_click=log_button_click(
                        f'{axis} {left_label} {value}{unit}',
                        lambda v=value, m=method_name: _execute_move(m, v),
                    ),
                ).classes('jog-btn')
                scanner_app.greyable_buttons.append(button)
            ui.button(
                'HOLD',
                color='red',
                on_click=log_button_click(
                    f'{axis} HOLD',
                    lambda: run.io_bound(hold_scanner),
                ),
            ).classes('jog-stop')
            for value, method_name in right_moves:
                button = ui.button(
                    f'{value}',
                    on_click=log_button_click(
                        f'{axis} {right_label} {value}{unit}',
                        lambda v=value, m=method_name: _execute_move(m, v),
                    ),
                ).classes('jog-btn')
                scanner_app.greyable_buttons.append(button)


def build_log_dialog():
    log_dialog = ui.dialog().props('full-width')
    with log_dialog, ui.card().classes('w-full flex flex-col').style(
        'height: 80vh; resize: both; overflow: auto; min-height: 400px;'
    ):
        with ui.row().classes('w-full justify-between items-center'):
            ui.label('System Log').classes('text-xl font-bold')
            ui.button(icon='close', on_click=log_dialog.close).props('flat')
        log_view = ui.log(max_lines=2000).classes(
            'w-full flex-1 overflow-auto border rounded p-2'
        ).style('white-space: pre')

    def tail_scanner_log():
        if log_handler is None:
            return
        try:
            while not log_handler.buffer.empty():
                line = log_handler.buffer.get_nowait()
                log_view.push(line)
        except Exception:
            pass

    ui.timer(0.5, tail_scanner_log)
    return log_dialog


def build_control_pane(log_dialog):
    global pos_r, pos_t, pos_z, pos_state, home_button, zero_button
    global measurement_start_button, measurement_stop_button

    with ui.column().classes('w-full h-full min-w-0 overflow-auto px-2 pt-3 pb-2'):
        _build_position_display()
        show_rehome = _get_app_bool("show_rehome_button", False)
        show_height_offset = _get_app_bool("show_height_offset_controls", True)
        use_alt_controls = _get_app_bool("use_alternative_motion_controls", False)
        height_offset_value = {'value': 0}

        async def _wait_for_home_settle(timeout_s: float = 5.0) -> bool:
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                if _scanner_has_alarm():
                    return False
                try:
                    if get_scanner().get_position() is not None:
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.1)
            return not _scanner_has_alarm()

        async def home_and_update():
            await safe_move(get_scanner().home)
            scanner_app.home_state['ok'] = await _wait_for_home_settle()
            _set_home_button_color(
                'green' if scanner_app.home_state['ok'] else 'orange'
            )

        if use_alt_controls:
            jog_step = {'value': 20}

            async def _execute_move(method_name: str):
                scanner = get_scanner()
                if not scanner:
                    ui.notify("Scanner not initialized", type='warning')
                    return
                method = getattr(scanner, method_name)
                await safe_move(method, jog_step['value'])

            def _alt_motion_button(icon: str, label: str, method_name: str):
                button = ui.button(
                    icon=icon,
                    on_click=log_button_click(
                        label,
                        lambda m=method_name: _execute_move(m),
                    ),
                ).classes('alt-motion-btn').props('color=primary no-caps')
                scanner_app.greyable_buttons.append(button)
                return button

            def _alt_axis_header(title: str):
                ui.html(
                    f'<span class="alt-axis-sign">-</span>'
                    f'<span class="alt-axis-title">{title}</span>'
                    f'<span class="alt-axis-sign">+</span>'
                ).classes('alt-axis-header')

            def _alt_command_button(text: str, icon: str, color: str, click):
                return ui.button(
                    text,
                    icon=icon,
                    color=color,
                    on_click=click,
                ).classes('alt-command-btn').props('no-caps')

            step_btns = []

            def set_step(val):
                jog_step['value'] = val
                for button in step_btns:
                    if getattr(button, '_step_val', None) == val:
                        button.props('color=primary text-color=white')
                    else:
                        button.props('color=blue-grey-8 text-color=white')

            with ui.column().classes('alt-motion-panel mb-3'):
                with ui.element('div').classes('alt-top-grid'):
                    ui.html('M<br>O<br>V<br>E').classes('alt-move-title')
                    with ui.element('div').classes('alt-axis-group alt-axis-phi'):
                        _alt_axis_header('PHI')
                        with ui.element('div').classes('alt-axis-buttons'):
                            _alt_motion_button('rotate_right', 'PHI CW', 'rotate_cw')
                            _alt_motion_button('rotate_left', 'PHI CCW', 'rotate_ccw')
                    with ui.element('div').classes('alt-axis-group alt-axis-radius'):
                        _alt_axis_header('RADIUS')
                        with ui.element('div').classes('alt-axis-buttons'):
                            _alt_motion_button('arrow_back', 'Radius IN', 'move_in')
                            _alt_motion_button('arrow_forward', 'Radius OUT', 'move_out')
                    with ui.element('div').classes('alt-axis-group alt-axis-height'):
                        _alt_axis_header('HEIGHT')
                        with ui.element('div').classes('alt-axis-buttons'):
                            _alt_motion_button('arrow_downward', 'Height DOWN', 'move_down')
                            _alt_motion_button('arrow_upward', 'Height UP', 'move_up')
                    with ui.element('div').classes('alt-status-controls'):
                        ui.element('div').classes('alt-status-spacer')
                        home_button = _alt_command_button(
                            'HOME',
                            'home',
                            'green' if scanner_app.home_state['ok'] else 'orange',
                            log_button_click('Home', home_and_update),
                        )

                with ui.element('div').classes('alt-step-row'):
                    ui.label('STEP').classes('alt-step-label')
                    for val in [0.1, 1, 5, 10, 20, 60, 120]:
                        label = str(val).rstrip('0').rstrip('.') if isinstance(val, float) else str(val)
                        button = ui.button(
                            label,
                            on_click=lambda e, v=val: set_step(v),
                        ).classes('alt-step-btn').props('no-caps')
                        button._step_val = val
                        step_btns.append(button)

                with ui.element('div').classes('alt-command-row'):
                    zero_button = _alt_command_button(
                        'ZERO',
                        'my_location',
                        'primary',
                        log_button_click(
                            'Zero NFS',
                            lambda: zero_nfs_then_apply_height_offset(
                                height_offset_value['value']
                            ),
                        ),
                    )
                    scanner_app.greyable_buttons.append(zero_button)
                    _alt_command_button(
                        'CLEAR ALARM',
                        'notifications',
                        'primary',
                        log_button_click(
                            'Clear Alarm',
                            lambda: run.io_bound(
                                get_scanner().clear_alarm if get_scanner() else None
                            ),
                        ),
                    )
                    _alt_command_button(
                        'RESET',
                        'warning',
                        'negative',
                        log_button_click(
                            'Reset Controller',
                            lambda: run.io_bound(
                                get_scanner().softreset if get_scanner() else None
                            ),
                        ),
                    )
                    _alt_command_button(
                        'HOLD',
                        'stop',
                        'negative',
                        log_button_click(
                            'Feed-Hold',
                            lambda: run.io_bound(
                                get_scanner().hold if get_scanner() else None
                            ),
                        ),
                    )

                set_step(20)
        else:
            async def _execute_step_move(method_name: str, value: float):
                scanner = get_scanner()
                if not scanner:
                    ui.notify("Scanner not initialized", type='warning')
                    return
                method = getattr(scanner, method_name)
                await safe_move(method, value)

            def _alt_command_button(text: str, icon: str, color: str, click):
                return ui.button(
                    text,
                    icon=icon,
                    color=color,
                    on_click=click,
                ).classes('alt-command-btn').props('no-caps')

            def _step_label(value: float) -> str:
                return str(value).rstrip('0').rstrip('.') if isinstance(value, float) else str(value)

            def _step_shade_class(value: float) -> str:
                return f'alt-jog-step-{str(value).replace(".", "_")}'

            def _step_shade_style(value: float) -> str:
                colors = {
                    1: ('#9fd0ff', '#08111f'),
                    5: ('#78b7f2', '#08111f'),
                    10: ('#5398df', '#ffffff'),
                    20: ('#3f84cf', '#ffffff'),
                    60: ('#2e72bd', '#ffffff'),
                    120: ('#174f98', '#ffffff'),
                }
                background, text = colors.get(value, ('#5398df', '#ffffff'))
                return (
                    f'background: {background} !important; '
                    f'background-color: {background} !important; '
                    f'color: {text} !important;'
                )

            def _panel_jog_row(axis: str, unit: str, left_label: str, right_label: str,
                               left_moves: list, right_moves: list):
                with ui.element('div').classes('alt-jog-row'):
                    with ui.element('div').classes('alt-jog-side alt-jog-side-left'):
                        ui.label(left_label).classes('alt-jog-direction')
                        with ui.element('div').classes('alt-jog-steps'):
                            for value, method_name in left_moves:
                                button = ui.button(
                                    _step_label(value),
                                    on_click=log_button_click(
                                        f'{axis} {left_label} {value}{unit}',
                                        lambda v=value, m=method_name: _execute_step_move(m, v),
                                    ),
                                ).classes(f'alt-step-btn alt-jog-step-btn {_step_shade_class(value)}').props('no-caps').style(_step_shade_style(value))
                                scanner_app.greyable_buttons.append(button)
                    ui.html(
                        f'<div class="alt-jog-axis">{axis}<div class="alt-jog-unit">{unit}</div></div>'
                    )
                    with ui.element('div').classes('alt-jog-side alt-jog-side-right'):
                        with ui.element('div').classes('alt-jog-steps'):
                            for value, method_name in right_moves:
                                button = ui.button(
                                    _step_label(value),
                                    on_click=log_button_click(
                                        f'{axis} {right_label} {value}{unit}',
                                        lambda v=value, m=method_name: _execute_step_move(m, v),
                                    ),
                                ).classes(f'alt-step-btn alt-jog-step-btn {_step_shade_class(value)}').props('no-caps').style(_step_shade_style(value))
                                scanner_app.greyable_buttons.append(button)
                        ui.label(right_label).classes('alt-jog-direction')

            with ui.column().classes('alt-motion-panel alt-jog-panel mb-3'):
                with ui.element('div').classes('alt-jog-grid'):
                    ui.html('M<br>O<br>V<br>E').classes('alt-move-title')
                    with ui.element('div').classes('alt-jog-rows'):
                        _panel_jog_row(
                            axis='PHI',
                            unit='Deg',
                            left_label='CW',
                            right_label='CCW',
                            left_moves=[
                                (120, 'rotate_cw'),
                                (60, 'rotate_cw'),
                                (20, 'rotate_cw'),
                                (10, 'rotate_cw'),
                                (5, 'rotate_cw'),
                                (1, 'rotate_cw'),
                            ],
                            right_moves=[
                                (1, 'rotate_ccw'),
                                (5, 'rotate_ccw'),
                                (10, 'rotate_ccw'),
                                (20, 'rotate_ccw'),
                                (60, 'rotate_ccw'),
                                (120, 'rotate_ccw'),
                            ],
                        )
                        _panel_jog_row(
                            axis='RADIUS',
                            unit='mm',
                            left_label='-',
                            right_label='+',
                            left_moves=[
                                (120, 'move_in'),
                                (60, 'move_in'),
                                (20, 'move_in'),
                                (10, 'move_in'),
                                (5, 'move_in'),
                                (1, 'move_in'),
                            ],
                            right_moves=[
                                (1, 'move_out'),
                                (5, 'move_out'),
                                (10, 'move_out'),
                                (20, 'move_out'),
                                (60, 'move_out'),
                                (120, 'move_out'),
                            ],
                        )
                        _panel_jog_row(
                            axis='HEIGHT',
                            unit='mm',
                            left_label='-',
                            right_label='+',
                            left_moves=[
                                (120, 'move_down'),
                                (60, 'move_down'),
                                (20, 'move_down'),
                                (10, 'move_down'),
                                (5, 'move_down'),
                                (1, 'move_down'),
                            ],
                            right_moves=[
                                (1, 'move_up'),
                                (5, 'move_up'),
                                (10, 'move_up'),
                                (20, 'move_up'),
                                (60, 'move_up'),
                                (120, 'move_up'),
                            ],
                        )

                command_count = 6 if show_rehome else 5
                with ui.element('div').classes(f'alt-command-row alt-command-row-{command_count}'):
                    home_button = _alt_command_button(
                        'HOME',
                        'home',
                        'green' if scanner_app.home_state['ok'] else 'orange',
                        log_button_click('Home', home_and_update),
                    )
                    zero_button = _alt_command_button(
                        'ZERO',
                        'my_location',
                        'primary',
                        log_button_click(
                            'Zero NFS',
                            lambda: zero_nfs_then_apply_height_offset(
                                height_offset_value['value']
                            ),
                        ),
                    )
                    scanner_app.greyable_buttons.append(zero_button)
                    _alt_command_button(
                        'CLEAR ALARM',
                        'notifications',
                        'primary',
                        log_button_click(
                            'Clear Alarm',
                            lambda: run.io_bound(
                                get_scanner().clear_alarm if get_scanner() else None
                            ),
                        ),
                    )
                    _alt_command_button(
                        'RESET',
                        'warning',
                        'negative',
                        log_button_click(
                            'Reset Controller',
                            lambda: run.io_bound(
                                get_scanner().softreset if get_scanner() else None
                            ),
                        ),
                    )
                    if show_rehome:
                        _alt_command_button(
                            'REHOME',
                            'home_repair_service',
                            'primary',
                            log_button_click(
                                'ReHome',
                                lambda: asyncio.create_task(safe_move(rehome)),
                            ),
                        )
                    _alt_command_button(
                        'HOLD',
                        'stop',
                        'negative',
                        log_button_click(
                            'Feed-Hold',
                            lambda: run.io_bound(
                                get_scanner().hold if get_scanner() else None
                            ),
                        ),
                    )

        if show_height_offset:
            with ui.button_group():
                height_input = ui.number(
                    label='Height Offset (mm)',
                    value=0,
                    format='%.2f',
                    on_change=lambda e: height_offset_value.update(
                        value=e.value or 0
                    ),
                )
                ui.button(
                    'Set height offset',
                    on_click=log_button_click(
                        'Set height offset',
                        lambda: run.io_bound(
                            get_scanner().set_speaker_center_above_stool
                            if get_scanner() else None,
                            height_input.value,
                        ),
                    ),
                )
        with ui.row().classes('items-center gap-4 mt-1'):
            scanner_app.greyable_buttons.append(ui.button(
                'Take single measurement',
                on_click=log_button_click(
                    'Take single measurement',
                    async_single_measurement_task,
                ),
            ).style('width: 260px'))

        with ui.row().classes('items-center gap-4 mt-1'):
            measurement_start_button = ui.button(
                'Start Measurements',
                on_click=log_button_click('Toggle measurement set', toggle_measurement_set),
            ).props('icon=play_arrow color=primary').style('width: 260px')
            measurement_stop_button = ui.button(
                'Stop Measurements',
                icon='stop',
                on_click=log_button_click(
                    'Stop measurement set',
                    stop_measurement_set,
                ),
            ).props('color=negative').style('width: 260px')
        _build_measurement_progress_panel()
        _update_measurement_buttons()

        with ui.row().classes('items-center mt-1 gap-4'):
            ui.button(
                'Show Logs',
                icon='list',
                on_click=log_dialog.open,
            )


def _get_app_bool(key: str, fallback: bool = False) -> bool:
    if scanner_app is None:
        return fallback
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.read(scanner_app.config_file)
    return parser.getboolean("app", key, fallback=fallback)


def _show_machine_coordinate_system() -> bool:
    return _get_app_bool("show_machine_coordinate_system", False)


def apply_position_display_config() -> None:
    show_mcs = _show_machine_coordinate_system()
    for title in position_display_titles:
        title.set_visibility(show_mcs)
    if wcs_position_display is not None:
        wcs_position_display.style(
            'grid-template-columns: 36px repeat(4, minmax(0, 1fr));'
            if show_mcs else
            'grid-template-columns: repeat(4, minmax(0, 1fr));'
        )
    if mcs_position_display is not None:
        mcs_position_display.set_visibility(show_mcs)


def _build_position_display():
    global pos_r, pos_t, pos_z, pos_state
    global mcs_pos_r, mcs_pos_t, mcs_pos_z, mcs_pos_state
    global mcs_position_display, wcs_position_display, position_display_titles

    display_style = (
        'display: grid; '
        'grid-template-columns: 36px repeat(4, minmax(0, 1fr)); '
        'gap: 0; '
        'max-width: 780px;'
    )
    housing_classes = (
        'w-full mb-3 bg-black rounded-lg border-2 border-gray-700 '
        'shadow-lg overflow-hidden'
    )

    position_display_titles = []

    with ui.element('div').classes(housing_classes).style(display_style) as display:
        wcs_position_display = display
        position_display_titles.append(
            _build_position_title('WCS', text_class='text-lime-200')
        )
        pos_t = _build_position_readout(
            'Phi deg', ' 888.88', ' 000.00'
        )
        pos_r = _build_position_readout(
            'Radius mm', ' 888.88', ' 000.00'
        )
        pos_z = _build_position_readout(
            'Height mm', ' 888.88', ' 000.00'
        )
        pos_state = _build_position_readout(
            'Status mode', 'XXXXXXXX', '   -   '
        )

    with ui.element('div').classes(housing_classes).style(display_style) as display:
        mcs_position_display = display
        _build_position_title('MCS', text_class='text-orange-200')
        mcs_pos_t = _build_position_readout(
            'Phi deg', ' 888.88', ' 000.00', text_class='text-orange-400'
        )
        mcs_pos_r = _build_position_readout(
            'Radius mm', ' 888.88', ' 000.00', text_class='text-orange-400'
        )
        mcs_pos_z = _build_position_readout(
            'Height mm', ' 888.88', ' 000.00', text_class='text-orange-400'
        )
        mcs_pos_state = _build_position_readout(
            'Status mode', 'XXXXXXXX', '   -   ', text_class='text-orange-400'
        )

    apply_position_display_config()


def _build_position_title(title, text_class='text-gray-300'):
    title_text = '<br>'.join(title)
    return ui.html(title_text).classes(
        f'h-full min-w-0 flex items-center justify-center text-xs font-bold '
        f'{text_class} uppercase leading-tight border-r border-gray-800'
    ).style(
        "font-family: 'Share Tech Mono', monospace; letter-spacing: 0;"
    )


def _build_position_readout(title, ghost, value, text_class='text-[#7eff00]'):
    label_classes = 'text-[0.68rem] font-bold text-gray-300 uppercase mb-1'
    bg_value_classes = 'text-3xl font-bold text-[#1a3300] absolute inset-0'
    value_classes = f'text-3xl font-bold {text_class} relative'
    value_style = (
        "font-family: 'Share Tech Mono', monospace; "
        "white-space: pre; letter-spacing: 0;"
    )

    with ui.element('div').classes(
        'min-w-0 px-2 py-1.5 text-center border-r border-gray-800 last:border-r-0'
    ):
        ui.label(title).classes(label_classes)
        with ui.element('div').classes('relative min-h-[34px] flex justify-center'):
            ui.label(ghost).classes(bg_value_classes).style(value_style)
            label = ui.label(value).classes(value_classes).style(value_style)
    return label


def initialize_app(config_file: str):
    global scanner_app, log_handler, log_handler_sink_id

    scanner_app = ScannerApp(config_file)
    log_handler = LogBuffer()
    log_handler_sink_id = None
    scanner_app.log_handler = log_handler


async def load_app(finish_splash, status_callback=None):
    def set_status(message):
        if status_callback is not None:
            status_callback(message)

    try:
        if scanner_app._is_loaded:
            set_status("Ready!")
            await finish_splash()
            return

        await run.io_bound(scanner_app.load_config, set_status)
        set_status(
            "Ready (scanner unavailable)" if scanner_app.load_warning else "Ready!"
        )

        if get_scanner():
            get_scanner().set_on_state_update_callback(update_scanner_position)
        if on_config_loaded:
            on_config_loaded()
        apply_project_directory_to_nfs()
        check_audio_device_ids_on_startup(
            scanner_app.config_file,
            scanner_app.reload_config_ui,
        )
        if scanner_app.load_warning:
            ui.notify(scanner_app.load_warning, type='warning')

    except Exception as exc:
        logger.error(f"Initialization error: {exc}")
        set_status(f"Error: {exc}")
        ui.notify(f"Initialization error: {exc}", type='negative')
    finally:
        await finish_splash()
