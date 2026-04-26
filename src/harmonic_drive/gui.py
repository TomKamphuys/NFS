from nicegui import app, ui, run
import argparse
import numpy as np
import asyncio
import time
import threading
import queue
import ctypes
import os
import sys
import soundfile as sf
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from fastapi.responses import FileResponse

if TYPE_CHECKING:
    from nfs.scanner import Scanner
    from nfs.nfs import NearFieldScanner

from loguru import logger

from nfs import NearFieldScannerFactory, ScannerFactory, Scanner, NearFieldScanner
from nfs.logging_config import setup_logging

# --- 7-segment look: load digital-ish font ---
ui.add_head_html('<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" rel="stylesheet">', shared=True)
ui.add_head_html('<link rel="icon" type="image/png" href="/images/icon.png">', shared=True)

# Global state
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
plot = None
fig = None
home_button = None
ir_fr_plot = None
fig_ir_fr = None


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
    
            # Create or reuse scanner
            update_status("Connecting to GRBL")
            self.scanner = ScannerFactory.create(self.config_file)
            
            # Create or reuse NFS
            update_status("Initializing Near Field Scanner & reading points")
            self.nfs = NearFieldScannerFactory.create(self.scanner, self.config_file)

            # Re-register callback to the new scanner instance
            if self.scanner:
                self.scanner.set_on_state_update_callback(update_scanner_position)
            
            self._is_loaded = True

    def reload_config_ui(self):
        try:
            with self._load_lock:
                self._is_loaded = False
            self.load_config()
            ui.notify("Configuration reloaded successfully", type='positive')
        except Exception as e:
            logger.error(f"Failed to reload configuration: {e}")
            ui.notify(f"Reload failed: {e}", type='negative')


def get_scanner() -> Scanner:
    return scanner_app.scanner


def get_nfs() -> NearFieldScanner:
    return scanner_app.nfs


# In-memory log buffer for the UI
class LogBuffer:
    def __init__(self, max_lines=2000):
        self.buffer = queue.Queue()
        self.max_lines = max_lines

    def write(self, message):
        # Loguru sends the message as a string (including newline)
        self.buffer.put(message.strip())


def log_button_click(label: str, handler):
    """Wrap a NiceGUI on_click handler to log the click and then run it (sync or async)."""

    async def _wrapped(*args, **kwargs):
        logger.info("UI click: {}", label)
        result = handler(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    return _wrapped


# --- Dedicated Audio Worker Thread (ASIO Fix) ---
audio_queue = queue.Queue()


def audio_worker():
    """A dedicated thread with COM initialization for picky ASIO drivers."""
    try:
        # Initialize COM for this thread (COINIT_APARTMENTTHREADED = 2)
        if sys.platform == 'win32':
            ctypes.windll.ole32.CoInitializeEx(None, 2)
            logger.info("Audio worker thread COM initialized.")
    except Exception as e:
        logger.warning(f"COM initialization failed: {e}")

    while True:
        item = audio_queue.get()
        if item is None:
            break

        func, args, done_event, loop = item
        try:
            func(*args)
        except StopIteration:
            logger.info("Measurement sequence completed: All points processed successfully.")
        except Exception as e:
            if "No more points" in str(e):
                logger.info("Measurement sequence completed: All points processed successfully.")
            else:
                logger.error(f"Audio worker failed: {e}")
        finally:
            loop.call_soon_threadsafe(done_event.set)
            audio_queue.task_done()

    try:
        if sys.platform == 'win32':
            ctypes.windll.ole32.CoUninitialize()
    except:
        pass


# Start the daemon worker thread immediately
worker_thread = threading.Thread(target=audio_worker, daemon=True)
worker_thread.start()

# --- CSS Styles ---
ui.add_css("""
@keyframes alarm_blink {
  0%   { opacity: 1; }
  50%  { opacity: 0.15; }
  100% { opacity: 1; }
}
.alarm_blink {
  animation: alarm_blink 0.6s linear infinite;
}
.jog-grid {
  display: grid;
  grid-template-columns: 64px repeat(4, 72px) 72px repeat(4, 72px);
  gap: 6px;
  align-items: center;
}
.jog-hdr {
  font-size: 0.85rem;
  font-weight: 600;
  color: #374151;
}
.jog-hdr-left  { grid-column: 2 / span 4; text-align: left; }
.jog-hdr-stop  { grid-column: 6; text-align: center; }
.jog-hdr-right { grid-column: 7 / span 4; text-align: right; }
.jog-axis {
  font-weight: 800;
  color: #111827;
  line-height: 1.05;
}
.jog-unit {
  font-size: 0.75rem;
  font-weight: 700;
  color: #374151;
  margin-top: 2px;
}
.jog-btn {
  width: 72px;
  min-height: 38px;
  font-weight: 800;
}
.jog-stop {
  width: 72px;
  min-height: 38px;
  font-weight: 900;
}
.cmd-row {
  display: grid;
  grid-template-columns: repeat(5, 120px);
  gap: 18px;
  align-items: stretch;
}
.cmd-btn {
  min-height: 56px;
  font-weight: 800;
  letter-spacing: 0.5px;
}
.cmd-btn-blue {
  background: #8fa9db !important;
  color: #0b1220 !important;
  border: 1px solid #5d6b86 !important;
}
""", shared=True)


def stop_nfs():
    logger.info('Stopping NFS and shutting down...')
    global is_playing
    try:
        # Signal audio thread to exit
        audio_queue.put(None)
        
        if scanner_app and scanner_app.nfs:
            scanner_app.nfs.shutdown()
        
        is_playing = False
        if play_button:
            play_button.props('icon=play_arrow')
            
        time.sleep(0.5)
        app.shutdown()
        os._exit(0)
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
        os._exit(1)


def hold_scanner():
    try:
        get_scanner().hold()
    except Exception as e:
        logger.error(f"Error during HOLD: {e}")


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
    except Exception as e:
        logger.error(f"Measurement task failed: {e}")
        ui.notify(f"Error: {e}", type='negative')
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
    except Exception as e:
        logger.error(f"Single measurement failed: {e}")
        ui.notify(f"Error: {e}", type='negative')
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
    except Exception as e:
        logger.error(f"Play sine failed: {e}")
        ui.notify(f"Error: {e}", type='negative')
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
    await run.io_bound(get_scanner().set_speaker_center_above_stool, height_value)


def load_measurement_data():
    measurement_dir = Path('./measurements')
    if not measurement_dir.exists():
        return None, None

    all_csv_files = list(measurement_dir.glob('*/measurement_points.csv'))
    if not all_csv_files:
        file_path = Path('measurement_points.csv')
        if not file_path.exists():
            file_path = Path('measurement_positions.csv')
            if not file_path.exists():
                return None, None
    else:
        file_path = max(all_csv_files, key=lambda f: f.stat().st_mtime)

    try:
        data = np.loadtxt(file_path, delimiter=',', skiprows=1)
        data = np.atleast_2d(data)
        if data.size == 0:
            return None, None

        r = data[:, 0]
        theta = data[:, 1]
        z = data[:, 2]
        elevation = np.degrees(np.arctan2(z, r))
        azimuth = theta
        return azimuth, elevation
    except Exception as e:
        logger.error(f"Error loading data: {e}")
        return None, None


def update_plot():
    azimuth, elevation = load_measurement_data()
    fig.clear()
    fig.set_layout_engine('constrained')
    ax = fig.add_subplot(111)
    if azimuth is not None and elevation is not None:
        scatter = ax.scatter(azimuth, elevation, c=elevation, cmap='viridis', marker='o', s=20)
        ax.set_xlabel('Azimuth (degrees)')
        ax.set_ylabel('Elevation (degrees)')
        ax.set_title('Measurement Points (Azimuth vs Elevation)')
        ax.set_xlim(-180, 180)
        ax.set_ylim(-90, 90)
        fig.colorbar(scatter, ax=ax, label='Elevation (degrees)')
        ax.grid(True, alpha=0.3)
    else:
        ax.text(0, 0, 'No data available', horizontalalignment='center', verticalalignment='center')
        ax.set_xlabel('Azimuth (degrees)')
        ax.set_ylabel('Elevation (degrees)')
        ax.set_title('Waiting for data...')
        ax.set_xlim(-180, 180)
        ax.set_ylim(-90, 90)
        ax.grid(True, alpha=0.3)
    plot.update()


def update_ir_fr_plots(ir_plot_container):
    # Find all _ir.wav files in current Recordings or any measurement session
    search_dirs = [Path('./Recordings')]
    measurement_dir = Path('./measurements')
    if measurement_dir.exists():
        search_dirs.extend(measurement_dir.glob('*/Recordings'))

    wav_files = []
    for d in search_dirs:
        if d.exists():
            wav_files.extend(list(d.glob('*_ir.wav')))

    if not wav_files:
        return
    latest_file = max(wav_files, key=lambda f: f.stat().st_mtime)
    try:
        ir, fs = sf.read(str(latest_file))
        if len(ir.shape) > 1:
            ir = ir[:, 0]
    except Exception as e:
        logger.error(f"Error loading IR: {e}")
        return
    zoom_ms = 15.0
    zoom_samples = int((zoom_ms / 1000.0) * fs)
    peak_idx = np.argmax(np.abs(ir))
    start_idx = max(0, peak_idx - int(zoom_samples / 4))
    end_idx = start_idx + zoom_samples
    ir_zoom = ir[start_idx:end_idx]
    time_axis = (np.arange(len(ir_zoom)) / fs) * 1000.0
    n_fft = 2 ** int(np.ceil(np.log2(len(ir))))
    fr = np.fft.rfft(ir, n=n_fft)
    freqs = np.fft.rfftfreq(n_fft, d=1 / fs)
    mag_db = 20 * np.log10(np.abs(fr) + 1e-12)

    with ir_plot_container:
        f = ir_plot_container.figure
        f.clear()
        f.set_layout_engine('constrained')
        ax1 = f.add_subplot(2, 1, 1)
        ax1.plot(time_axis, ir_zoom)
        ax1.set_title(f'Impulse Response (Zoomed): {latest_file.name}')
        ax1.set_xlabel('Time (ms)')
        ax1.set_ylabel('Amplitude')
        ax1.grid(True, alpha=0.3)
        ax2 = f.add_subplot(2, 1, 2)
        ax2.semilogx(freqs, mag_db)
        ax2.set_title(f'Frequency Response: {latest_file.name}')
        ax2.set_xlabel('Frequency (Hz)')
        ax2.set_ylabel('Magnitude (dB)')
        ax2.set_xlim(20, 20000)
        ax2.set_ylim(-60, 10)
        ax2.grid(True, which='both', alpha=0.3)
        ir_plot_container.update()


async def watch_file(main_plot, ir_plot):
    measurement_dir = Path('./measurements')
    last_mtime = 0
    last_file_path = None
    last_ir_mtime = 0

    while True:
        try:
            # Check for CSV updates (Scanner position/grid)
            all_csv_files = list(measurement_dir.glob('*/measurement_points.csv'))
            root_csv = Path('measurement_points.csv')
            if root_csv.exists(): all_csv_files.append(root_csv)
            root_pos_csv = Path('measurement_positions.csv')
            if root_pos_csv.exists(): all_csv_files.append(root_pos_csv)
            
            if all_csv_files:
                current_file_path = max(all_csv_files, key=lambda f: f.stat().st_mtime)
                current_mtime = current_file_path.stat().st_mtime
                if current_mtime != last_mtime or current_file_path != last_file_path:
                    last_mtime = current_mtime
                    last_file_path = current_file_path
                    update_plot()
                    update_ir_fr_plots(ir_plot)

            # Check for IR updates (New measurements)
            search_dirs = [Path('./Recordings')]
            if measurement_dir.exists():
                search_dirs.extend(measurement_dir.glob('*/Recordings'))
            
            latest_ir_mtime = 0
            for d in search_dirs:
                if d.exists():
                    wavs = list(d.glob('*_ir.wav'))
                    if wavs:
                        mtime = max(f.stat().st_mtime for f in wavs)
                        latest_ir_mtime = max(latest_ir_mtime, mtime)
            
            if latest_ir_mtime > last_ir_mtime:
                last_ir_mtime = latest_ir_mtime
                update_ir_fr_plots(ir_plot)

            await asyncio.sleep(1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error watching file: {e}")
            await asyncio.sleep(1)


def _scanner_has_alarm() -> bool:
    try:
        st = get_scanner().get_state()
        if st is None: return False
        name = getattr(st, 'name', str(st))
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
        st = get_scanner().get_state()
        if st is None: return None
        if hasattr(st, 'name'): return st.name
        return str(st).split('.')[-1]
    except Exception:
        return None


def update_scanner_position(pos=None, state=None):
    def do_update():
        nonlocal pos, state
        if pos is None: pos = get_scanner().get_position()
        if pos is not None:
            pos_r.set_text(f'{pos.r():7.2f}')
            pos_t.set_text(f'{pos.t():7.2f}')
            pos_z.set_text(f'{pos.z():7.2f}')
        else:
            pos_r.set_text('   —   ')
            pos_t.set_text('   —   ')
            pos_z.set_text('   —   ')
        if state is None:
            raw_state = _get_raw_state_string()
        else:
            raw_state = state.name if hasattr(state, 'name') else str(state).split('.')[-1]
        if raw_state is not None:
            pos_state.set_text(f'{raw_state:^8}')
        else:
            pos_state.set_text('   —   ')
        if _scanner_has_alarm():
            pos_state.classes(remove='text-[#7eff00]').classes(add='text-red-600 alarm_blink')
            scanner_app.home_state['ok'] = False
            _set_home_button_color('orange')
        else:
            pos_state.classes(remove='text-red-600 alarm_blink').classes(add='text-[#7eff00]')
            _set_home_button_color('green' if scanner_app.home_state['ok'] else 'orange')

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
    def _execute_move(method_name: str, value: float):
        scanner = get_scanner()
        if not scanner:
            ui.notify("Scanner not initialized", type='warning')
            return
        method = getattr(scanner, method_name)
        safe_move(method, value)

    with ui.column().classes('w-full'):
        with ui.element('div').classes('jog-grid'):
            ui.label('')
            ui.label(left_label).classes('jog-hdr jog-hdr-left')
            ui.label('STOP').classes('jog-hdr jog-hdr-stop')
            ui.label(right_label).classes('jog-hdr jog-hdr-right')
        with ui.element('div').classes('jog-grid'):
            ui.html(f'<div class="jog-axis">{axis}:<div class="jog-unit">{unit}</div></div>')
            for value, method_name in left_moves:
                b = ui.button(f'{value}', on_click=log_button_click(f'{axis} {left_label} {value}{unit}', lambda v=value, m=method_name: _execute_move(m, v))).classes('jog-btn')
                scanner_app.greyable_buttons.append(b)
            ui.button('STOP', color='red', on_click=log_button_click(f'{axis} STOP (HOLD)', lambda: run.io_bound(hold_scanner))).classes('jog-stop')
            for value, method_name in right_moves:
                b = ui.button(f'{value}', on_click=log_button_click(f'{axis} {right_label} {value}{unit}', lambda v=value, m=method_name: _execute_move(m, v))).classes('jog-btn')
                scanner_app.greyable_buttons.append(b)


@ui.page('/')
def main_page():
    global log_handler, play_button, level_input, freq_input, dur_input, pos_r, pos_t, pos_z, pos_state, plot, fig, home_button, ir_fr_plot, fig_ir_fr
    
    with ui.element('div').style('position: fixed; top: 25%; left: 25%; width: 50%; height: 50%; z-index: 9999; background: transparent; opacity: 1;') as splash:
        ui.image('/images/splash.png').style('width: 100%; height: 100%; object-fit: cover; position: absolute; top: 0; left: 0;')
        with ui.column().classes('w-full items-start justify-end h-full p-8 relative'):
            with ui.row().classes('items-center'):
                status_label = ui.label('Initializing').classes('text-xl font-bold text-white shadow-sm')
                dots_label = ui.label('.').classes('text-xl font-bold text-white')
            
            def update_dots():
                dots_label.set_text('.' * ((len(dots_label.text) % 3) + 1))
            ui.timer(0.5, update_dots)

    async def finish_splash():
        ui.timer(2.0, lambda: splash.style('transition: opacity 1s; opacity: 0;'))
        def safe_delete():
            try:
                splash.delete()
            except Exception:
                pass
        ui.timer(3.0, safe_delete)

    async def load_app():
        try:
            # Check if already loaded to avoid multiple splash screen sequences if triggered redundantly
            if scanner_app._is_loaded:
                status_label.set_text("Ready!")
                await finish_splash()
                return

            # Run load_config in a thread to avoid blocking the event loop
            await run.io_bound(scanner_app.load_config, lambda msg: status_label.set_text(msg))
            status_label.set_text("Ready!")
            
            # Register initial callback (now that scanner exists)
            if get_scanner():
                get_scanner().set_on_state_update_callback(update_scanner_position)
            
            # Update plots if NFS is ready
            if get_nfs():
                update_ir_fr_plots(ir_fr_plot)
                
        except Exception as e:
            logger.error(f"Initialization error: {e}")
            status_label.set_text(f"Error: {e}")
            ui.notify(f"Initialization error: {e}", type='negative')
        finally:
            await finish_splash()

    # Start loading as soon as we connect
    ui.timer(0, load_app, once=True)

    log_dialog = ui.dialog().props('full-width')
    with log_dialog, ui.card().classes('w-full flex flex-col').style('height: 80vh; resize: both; overflow: auto; min-height: 400px;'):
        with ui.row().classes('w-full justify-between items-center'):
            ui.label('System Log').classes('text-xl font-bold')
            ui.button(icon='close', on_click=log_dialog.close).props('flat')
        log_view = ui.log(max_lines=2000).classes('w-full flex-1 overflow-auto border rounded p-2').style('white-space: pre')

    with ui.splitter(value=50).classes('w-full h-screen items-stretch') as splitter:
        with splitter.before:
            with ui.column().classes('w-full h-full min-w-0 overflow-auto px-2 py-2'):
                with ui.row().classes('w-full items-start gap-4 mb-4'):
                    with ui.column().classes('flex-1'):
                        add_jog_row(axis='PHI', left_label='CW', right_label='CCW', unit='Deg',
                                    left_moves=[(120, 'rotate_cw'), (60, 'rotate_cw'), (10, 'rotate_cw'), (1, 'rotate_cw')],
                                    right_moves=[(1, 'rotate_ccw'), (10, 'rotate_ccw'), (60, 'rotate_ccw'), (120, 'rotate_ccw')])
                        add_jog_row(axis='R', left_label='IN', right_label='OUT', unit='mm',
                                    left_moves=[(120, 'move_in'), (60, 'move_in'), (10, 'move_in'), (1, 'move_in')],
                                    right_moves=[(1, 'move_out'), (10, 'move_out'), (60, 'move_out'), (120, 'move_out')])
                        add_jog_row(axis='Z', left_label='DOWN', right_label='UP', unit='mm',
                                    left_moves=[(120, 'move_down'), (60, 'move_down'), (10, 'move_down'), (1, 'move_down')],
                                    right_moves=[(1, 'move_up'), (10, 'move_up'), (60, 'move_up'), (120, 'move_up')])
                    ui.image('/images/splash.png').classes('w-64 rounded-lg shadow-lg self-center')

                async def _wait_for_home_settle(timeout_s: float = 5.0) -> bool:
                    deadline = time.time() + timeout_s
                    while time.time() < deadline:
                        if _scanner_has_alarm(): return False
                        try:
                            if get_scanner().get_position() is not None: return True
                        except: pass
                        await asyncio.sleep(0.1)
                    return not _scanner_has_alarm()

                async def home_and_update():
                    await safe_move(get_scanner().home)
                    scanner_app.home_state['ok'] = await _wait_for_home_settle()
                    _set_home_button_color('green' if scanner_app.home_state['ok'] else 'orange')

                with ui.element('div').classes('cmd-row w-full justify-start mt-1'):
                    home_button = ui.button('HOME', color='orange', on_click=log_button_click('Home', home_and_update)).classes('cmd-btn')
                    ui.button('Clear\nAlarm', on_click=log_button_click('Clear Alarm', lambda: run.io_bound(get_scanner().clear_alarm if get_scanner() else None))).classes('cmd-btn cmd-btn-blue')
                    ui.button('Soft\nReset', on_click=log_button_click('Soft Reset', lambda: run.io_bound(get_scanner().softreset if get_scanner() else None))).classes('cmd-btn cmd-btn-blue')
                    ui.button('REHOME', on_click=log_button_click('ReHome', lambda: safe_move(rehome))).classes('cmd-btn cmd-btn-blue')
                    ui.button('HOLD', color='red', on_click=log_button_click('Hold', lambda: run.io_bound(get_scanner().hold if get_scanner() else None))).classes('cmd-btn')

                with ui.button_group():
                    height_input = ui.number(label='Height Offset (mm)', value=0, format='%.2f')
                    ui.button('Set height offset', on_click=log_button_click('Set height offset', lambda: run.io_bound(get_scanner().set_speaker_center_above_stool if get_scanner() else None, height_input.value)))
                scanner_app.greyable_buttons.append(ui.button('Zero NFS', color='orange', on_click=log_button_click('Zero NFS', lambda: zero_nfs_then_apply_height_offset(height_input.value))))

                with ui.button_group():
                    scanner_app.greyable_buttons.append(ui.button('Start measurements', on_click=log_button_click('Start measurements', async_task)))
                    scanner_app.greyable_buttons.append(ui.button('Take single measurement', on_click=log_button_click('Take single measurement', async_single_measurement_task)))

                with ui.row().classes('items-center mt-1 gap-4'):
                    ui.button('Reload Config', icon='sync', on_click=scanner_app.reload_config_ui).classes('bg-blue-200 text-blue-900')
                    with ui.row().classes('items-center gap-2'):
                        level_input = ui.number('Level (dBFS)', value=-20, format='%.1f').props('dense outlined').classes('w-32')
                        freq_input = ui.number('Frequency (Hz)', value=1000, format='%d').props('dense outlined').classes('w-32')
                        dur_input = ui.number('Duration (s)', value=None, format='%.1f').props('dense outlined').classes('w-32')
                        play_button = ui.button(icon='play_arrow', on_click=log_button_click('Play Sine', async_play_sine_task)).props('round')
                        ui.button('Shutdown Program', color='red', on_click=log_button_click('Shutdown Program', stop_nfs))
                    ui.button('Show Logs', icon='list', on_click=log_dialog.open).classes('ml-2')

                with ui.row().classes('w-full justify-start items-center gap-4'):
                    with ui.row().classes('gap-4 items-center'):
                        card_classes = 'p-2 items-center bg-black rounded-lg border-2 border-gray-700 w-48'
                        label_classes = 'text-xs font-bold text-gray-300 uppercase tracking-widest mb-1'
                        bg_value_classes = 'text-4xl font-bold text-[#1a3300] absolute'
                        value_classes = 'text-4xl font-bold text-[#7eff00] relative'
                        value_style = "font-family: 'Share Tech Mono', monospace; white-space: pre;"
                        unit_classes = 'text-xs font-bold text-gray-400 mt-1'
                        with ui.card().classes(card_classes):
                            ui.label('R (Radius)').classes(label_classes)
                            with ui.element('div').classes('relative'):
                                ui.label(' 888.88').classes(bg_value_classes).style(value_style)
                                pos_r = ui.label(' 000.00').classes(value_classes).style(value_style)
                            ui.label('mm').classes(unit_classes)
                        with ui.card().classes(card_classes):
                            ui.label('T (Theta)').classes(label_classes)
                            with ui.element('div').classes('relative'):
                                ui.label(' 888.88').classes(bg_value_classes).style(value_style)
                                pos_t = ui.label(' 000.00').classes(value_classes).style(value_style)
                            ui.label('°').classes(unit_classes)
                        with ui.card().classes(card_classes):
                            ui.label('Z (Height)').classes(label_classes)
                            with ui.element('div').classes('relative'):
                                ui.label(' 888.88').classes(bg_value_classes).style(value_style)
                                pos_z = ui.label(' 000.00').classes(value_classes).style(value_style)
                            ui.label('mm').classes(unit_classes)
                        with ui.card().classes(card_classes):
                            ui.label('Status').classes(label_classes)
                            with ui.element('div').classes('relative'):
                                ui.label('XXXXXXXX').classes(bg_value_classes).style(value_style)
                                pos_state = ui.label('   —   ').classes(value_classes).style(value_style)
                            ui.label('Mode').classes(unit_classes)

                plot = ui.matplotlib(figsize=(16, 7)).classes('w-full flex-1')
                with plot.figure as f:
                    fig = f
                    update_plot()

        with splitter.after:
            with ui.column().classes('w-full h-full min-w-0 flex flex-col p-2'):
                ui.label('Acoustic Analysis').classes('text-xl font-bold mb-1')
                ir_fr_plot = ui.matplotlib(figsize=(16, 12)).classes('w-full flex-1')
                with ir_fr_plot.figure as f_ir_fr:
                    fig_ir_fr = f_ir_fr
                ui.button('Refresh Plots', icon='refresh', on_click=lambda: update_ir_fr_plots(ir_fr_plot)).classes('mt-2')

                def tail_scanner_log():
                    if log_handler is None: return
                    try:
                        while not log_handler.buffer.empty():
                            line = log_handler.buffer.get_nowait()
                            log_view.push(line)
                    except: pass
                ui.timer(0.5, tail_scanner_log)

    ui.timer(1.0, lambda: watch_file(plot, ir_fr_plot))
    
    # Initialization is now handled by load_app in main_page()


def main():
    global scanner_app, log_handler
    parser = argparse.ArgumentParser(description='Near-field scanner UI')
    parser.add_argument('--config', default='config.ini', help='Path to the configuration file')
    args, _ = parser.parse_known_args()
    config_file = args.config

    scanner_app = ScannerApp(config_file)

    log_handler = LogBuffer()
    scanner_app.log_handler = log_handler
    logger.add(log_handler.write, level="INFO", format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}")

    static_images_path = os.path.join(os.getcwd(), 'images')
    if os.path.exists(static_images_path):
        app.add_static_files('/images', static_images_path)

    ui.run(reload=False, title='HALS', favicon=os.path.join(static_images_path, 'icon.png') if os.path.exists(os.path.join(static_images_path, 'icon.png')) else None)


if __name__ in {"__main__", "__mp_main__"}:
    main()
