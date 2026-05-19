import asyncio
import ctypes
import os
import queue
import sys
import threading
import time
from typing import TYPE_CHECKING

from loguru import logger
from nicegui import app, run, ui

from harmonic_drive.config_editor import (
    open_config_editor,
    set_file_measurement_points_filename,
)
from nfs import NearFieldScannerFactory, ScannerFactory
from nfs.logging_config import setup_logging

if TYPE_CHECKING:
    from nfs.nfs import NearFieldScanner
    from nfs.scanner import Scanner


scanner_app = None
log_handler = None
is_playing = False
play_button = None
level_input = None
freq_input = None
dur_input = None
pos_r = None
pos_t = None
pos_z = None
pos_state = None
home_button = None

on_config_loaded = None


def set_on_config_loaded(callback):
    global on_config_loaded
    on_config_loaded = callback


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

    def load_config(self, status_callback=None):
        with self._load_lock:
            if self._is_loaded:
                return

            def update_status(msg):
                logger.info(msg)
                if status_callback:
                    status_callback(msg)

            update_status("(Re)loading configuration")
            setup_logging(self.config_file, project_name="HarmonicDrive")
            update_status("Connecting to GRBL")
            self.scanner = ScannerFactory.create(self.config_file)
            update_status("Initializing Near Field Scanner & reading points")
            self.nfs = NearFieldScannerFactory.create(self.scanner, self.config_file)

            if self.scanner:
                self.scanner.set_on_state_update_callback(update_scanner_position)

            _log_built_object_tree(self.scanner, self.nfs, self.config_file)
            self._is_loaded = True

    def reload_config_ui(self):
        try:
            with self._load_lock:
                self._is_loaded = False
            self.load_config()
            if on_config_loaded:
                on_config_loaded()
            ui.notify("Configuration reloaded successfully", type='positive')
        except Exception as exc:
            logger.error(f"Failed to reload configuration: {exc}")
            ui.notify(f"Reload failed: {exc}", type='negative')


def get_scanner() -> "Scanner":
    return scanner_app.scanner


def get_nfs() -> "NearFieldScanner":
    return scanner_app.nfs


def use_generated_grid_file(filename: str):
    try:
        section = set_file_measurement_points_filename(
            scanner_app.config_file,
            filename,
            scanner_app.reload_config_ui,
        )
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

        func, args, done_event, loop = item
        try:
            func(*args)
        except StopIteration:
            logger.info("Measurement sequence completed: all points processed.")
        except Exception as exc:
            if "No more points" in str(exc):
                logger.info("Measurement sequence completed: all points processed.")
            else:
                logger.error(f"Audio worker failed: {exc}")
        finally:
            loop.call_soon_threadsafe(done_event.set)
            audio_queue.task_done()

    try:
        if sys.platform == 'win32':
            ctypes.windll.ole32.CoUninitialize()
    except Exception:
        pass


worker_thread = threading.Thread(target=audio_worker, daemon=True)
worker_thread.start()


def stop_nfs():
    logger.info('Stopping NFS and shutting down...')
    global is_playing
    try:
        audio_queue.put(None)
        if scanner_app and scanner_app.nfs:
            scanner_app.nfs.shutdown()
        is_playing = False
        if play_button:
            play_button.props('icon=play_arrow')
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


def rehome():
    get_scanner().softreset()
    time.sleep(1)
    get_scanner().clear_alarm()
    time.sleep(1)
    get_scanner().home()


async def async_task():
    ui.notify('Measurement started')
    for button in scanner_app.greyable_buttons:
        button.disable()
    try:
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        audio_queue.put((get_nfs().take_measurement_set, (), done, loop))
        await done.wait()
    except Exception as exc:
        logger.error(f"Measurement task failed: {exc}")
        ui.notify(f"Error: {exc}", type='negative')
    finally:
        ui.notify('Measurement finished')
        for button in scanner_app.greyable_buttons:
            button.enable()


async def async_single_measurement_task():
    ui.notify('Single measurement started')
    for button in scanner_app.greyable_buttons:
        button.disable()
    try:
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        audio_queue.put((get_nfs().take_single_measurement, (), done, loop))
        await done.wait()
    except Exception as exc:
        logger.error(f"Single measurement failed: {exc}")
        ui.notify(f"Error: {exc}", type='negative')
    finally:
        ui.notify('Single measurement finished')
        for button in scanner_app.greyable_buttons:
            button.enable()


async def async_play_sine_task():
    global is_playing
    if is_playing:
        get_nfs().stop_sine()
        is_playing = False
        if play_button:
            play_button.props('icon=play_arrow')
        return

    level = level_input.value if level_input.value is not None else -20.0
    freq = freq_input.value if freq_input.value is not None else 1000.0
    dur = float(dur_input.value) if dur_input.value is not None else None

    is_playing = True
    if play_button:
        play_button.props('icon=stop')

    try:
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        audio_queue.put((get_nfs().play_sine, (freq, level, dur), done, loop))
        await done.wait()
    except Exception as exc:
        logger.error(f"Play sine failed: {exc}")
        ui.notify(f"Error: {exc}", type='negative')
    finally:
        if dur is not None:
            is_playing = False
            if play_button:
                play_button.props('icon=play_arrow')


async def safe_move(func, *args):
    for button in scanner_app.greyable_buttons:
        button.disable()
    try:
        await run.io_bound(func, *args)
    finally:
        for button in scanner_app.greyable_buttons:
            button.enable()


async def zero_nfs_then_apply_height_offset(height_value: float):
    await run.io_bound(get_scanner().set_as_zero)
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


def update_scanner_position(pos=None, state=None):
    def do_update():
        nonlocal pos, state
        if pos is None:
            pos = get_scanner().get_position()
        if pos is not None:
            pos_r.set_text(f'{pos.r():7.2f}')
            pos_t.set_text(f'{pos.t():7.2f}')
            pos_z.set_text(f'{pos.z():7.2f}')
        else:
            pos_r.set_text('   -   ')
            pos_t.set_text('   -   ')
            pos_z.set_text('   -   ')
        if state is None:
            raw_state = _get_raw_state_string()
        else:
            raw_state = state.name if hasattr(state, 'name') \
                else str(state).split('.')[-1]
        pos_state.set_text(f'{raw_state:^8}' if raw_state else '   -   ')
        if _scanner_has_alarm():
            pos_state.classes(remove='text-[#7eff00]').classes(
                add='text-red-600 alarm_blink'
            )
            scanner_app.home_state['ok'] = False
            _set_home_button_color('orange')
        else:
            pos_state.classes(remove='text-red-600 alarm_blink').classes(
                add='text-[#7eff00]'
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
            ui.label('STOP').classes('jog-hdr jog-hdr-stop')
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
                'STOP',
                color='red',
                on_click=log_button_click(
                    f'{axis} STOP (HOLD)',
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
    global play_button, level_input, freq_input, dur_input
    global pos_r, pos_t, pos_z, pos_state, home_button

    with ui.column().classes('w-full h-full min-w-0 overflow-auto px-2 py-2'):
        with ui.row().classes('w-full items-start gap-4 mb-4'):
            with ui.column().classes('flex-1'):
                add_jog_row(
                    axis='PHI',
                    left_label='CW',
                    right_label='CCW',
                    unit='Deg',
                    left_moves=[
                        (120, 'rotate_cw'),
                        (60, 'rotate_cw'),
                        (10, 'rotate_cw'),
                        (1, 'rotate_cw'),
                    ],
                    right_moves=[
                        (1, 'rotate_ccw'),
                        (10, 'rotate_ccw'),
                        (60, 'rotate_ccw'),
                        (120, 'rotate_ccw'),
                    ],
                )
                add_jog_row(
                    axis='R',
                    left_label='IN',
                    right_label='OUT',
                    unit='mm',
                    left_moves=[
                        (120, 'move_in'),
                        (60, 'move_in'),
                        (10, 'move_in'),
                        (1, 'move_in'),
                    ],
                    right_moves=[
                        (1, 'move_out'),
                        (10, 'move_out'),
                        (60, 'move_out'),
                        (120, 'move_out'),
                    ],
                )
                add_jog_row(
                    axis='Z',
                    left_label='DOWN',
                    right_label='UP',
                    unit='mm',
                    left_moves=[
                        (120, 'move_down'),
                        (60, 'move_down'),
                        (10, 'move_down'),
                        (1, 'move_down'),
                    ],
                    right_moves=[
                        (1, 'move_up'),
                        (10, 'move_up'),
                        (60, 'move_up'),
                        (120, 'move_up'),
                    ],
                )
            ui.image('/images/splash.png').classes(
                'w-64 rounded-lg shadow-lg self-center'
            )

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

        with ui.element('div').classes('cmd-row w-full justify-start mt-1'):
            home_button = ui.button(
                'HOME',
                color='orange',
                on_click=log_button_click('Home', home_and_update),
            ).classes('cmd-btn')
            ui.button(
                'Clear\nAlarm',
                on_click=log_button_click(
                    'Clear Alarm',
                    lambda: run.io_bound(
                        get_scanner().clear_alarm if get_scanner() else None
                    ),
                ),
            ).classes('cmd-btn cmd-btn-blue')
            ui.button(
                'Soft\nReset',
                on_click=log_button_click(
                    'Soft Reset',
                    lambda: run.io_bound(
                        get_scanner().softreset if get_scanner() else None
                    ),
                ),
            ).classes('cmd-btn cmd-btn-blue')
            ui.button(
                'REHOME',
                on_click=log_button_click(
                    'ReHome',
                    lambda: asyncio.create_task(safe_move(rehome)),
                ),
            ).classes('cmd-btn cmd-btn-blue')
            ui.button(
                'HOLD',
                color='red',
                on_click=log_button_click(
                    'Hold',
                    lambda: run.io_bound(
                        get_scanner().hold if get_scanner() else None
                    ),
                ),
            ).classes('cmd-btn')

        with ui.button_group():
            height_input = ui.number(
                label='Height Offset (mm)',
                value=0,
                format='%.2f',
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
        scanner_app.greyable_buttons.append(ui.button(
            'Zero NFS',
            color='orange',
            on_click=log_button_click(
                'Zero NFS',
                lambda: zero_nfs_then_apply_height_offset(height_input.value),
            ),
        ))

        with ui.button_group():
            scanner_app.greyable_buttons.append(ui.button(
                'Start measurements',
                on_click=log_button_click('Start measurements', async_task),
            ))
            scanner_app.greyable_buttons.append(ui.button(
                'Take single measurement',
                on_click=log_button_click(
                    'Take single measurement',
                    async_single_measurement_task,
                ),
            ))

        with ui.row().classes('items-center mt-1 gap-4'):
            ui.button(
                'Edit Config',
                icon='edit',
                on_click=lambda: open_config_editor(
                    scanner_app.config_file,
                    scanner_app.reload_config_ui,
                ),
            ).classes('bg-blue-200 text-blue-900')
            with ui.row().classes('items-center gap-2'):
                level_input = ui.number(
                    'Level (dBFS)',
                    value=-20,
                    format='%.1f',
                ).props('dense outlined').classes('w-32')
                freq_input = ui.number(
                    'Frequency (Hz)',
                    value=1000,
                    format='%d',
                ).props('dense outlined').classes('w-32')
                dur_input = ui.number(
                    'Duration (s)',
                    value=None,
                    format='%.1f',
                ).props('dense outlined').classes('w-32')
                play_button = ui.button(
                    icon='play_arrow',
                    on_click=log_button_click('Play Sine', async_play_sine_task),
                ).props('round')
                ui.button(
                    'Shutdown Program',
                    color='red',
                    on_click=log_button_click('Shutdown Program', stop_nfs),
                )
            ui.button(
                'Show Logs',
                icon='list',
                on_click=log_dialog.open,
            ).classes('ml-2')

        with ui.row().classes('w-full justify-start items-center gap-4'):
            with ui.row().classes('gap-4 items-center'):
                _build_position_card('R (Radius)', ' 888.88', ' 000.00', 'mm', 'r')
                _build_position_card('P (Phi)', ' 888.88', ' 000.00', 'Deg', 't')
                _build_position_card('Z (Height)', ' 888.88', ' 000.00', 'mm', 'z')
                _build_position_card('Status', 'XXXXXXXX', '   -   ', 'Mode', 'state')


def _build_position_card(title, ghost, value, unit, target):
    global pos_r, pos_t, pos_z, pos_state

    card_classes = 'p-2 items-center bg-black rounded-lg border-2 border-gray-700 w-48'
    label_classes = 'text-xs font-bold text-gray-300 uppercase tracking-widest mb-1'
    bg_value_classes = 'text-4xl font-bold text-[#1a3300] absolute'
    value_classes = 'text-4xl font-bold text-[#7eff00] relative'
    value_style = "font-family: 'Share Tech Mono', monospace; white-space: pre;"
    unit_classes = 'text-xs font-bold text-gray-400 mt-1'

    with ui.card().classes(card_classes):
        ui.label(title).classes(label_classes)
        with ui.element('div').classes('relative'):
            ui.label(ghost).classes(bg_value_classes).style(value_style)
            label = ui.label(value).classes(value_classes).style(value_style)
        ui.label(unit).classes(unit_classes)

    if target == 'r':
        pos_r = label
    elif target == 't':
        pos_t = label
    elif target == 'z':
        pos_z = label
    else:
        pos_state = label


def initialize_app(config_file: str):
    global scanner_app, log_handler

    scanner_app = ScannerApp(config_file)
    log_handler = LogBuffer()
    scanner_app.log_handler = log_handler
    logger.add(
        log_handler.write,
        level="INFO",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
            "{name}:{function}:{line} - {message}"
        ),
    )


async def load_app(status_label, finish_splash):
    try:
        if scanner_app._is_loaded:
            status_label.set_text("Ready!")
            await finish_splash()
            return

        await run.io_bound(scanner_app.load_config, status_label.set_text)
        status_label.set_text("Ready!")

        if get_scanner():
            get_scanner().set_on_state_update_callback(update_scanner_position)
        if on_config_loaded:
            on_config_loaded()

    except Exception as exc:
        logger.error(f"Initialization error: {exc}")
        status_label.set_text(f"Error: {exc}")
        ui.notify(f"Initialization error: {exc}", type='negative')
    finally:
        await finish_splash()
