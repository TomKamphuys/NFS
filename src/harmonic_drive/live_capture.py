import asyncio
from pathlib import Path

import numpy as np
import soundfile as sf
from loguru import logger
from nicegui import ui


measurement_position_plot = None
impulse_response_plot = None
frequency_response_plot = None


def _find_latest_ir_file():
    search_dirs = [Path('./Recordings')]
    measurement_dir = Path('./measurements')
    if measurement_dir.exists():
        search_dirs.extend(measurement_dir.glob('*/Recordings'))

    wav_files = []
    for directory in search_dirs:
        if directory.exists():
            wav_files.extend(list(directory.glob('*_ir.wav')))

    if not wav_files:
        return None
    return max(wav_files, key=lambda file: file.stat().st_mtime)


def _load_latest_ir():
    latest_file = _find_latest_ir_file()
    if latest_file is None:
        return None, None, None

    try:
        ir, fs = sf.read(str(latest_file))
        if len(ir.shape) > 1:
            ir = ir[:, 0]
        return latest_file, ir, fs
    except Exception as exc:
        logger.error(f"Error loading IR: {exc}")
        return None, None, None


def load_measurement_data():
    measurement_dir = Path('./measurements')
    all_csv_files = []
    if measurement_dir.exists():
        all_csv_files.extend(measurement_dir.glob('*/measurement_points.csv'))

    root_csv = Path('measurement_points.csv')
    if root_csv.exists():
        all_csv_files.append(root_csv)

    root_pos_csv = Path('measurement_positions.csv')
    if root_pos_csv.exists():
        all_csv_files.append(root_pos_csv)

    if not all_csv_files:
        return None, None

    file_path = max(all_csv_files, key=lambda file: file.stat().st_mtime)

    try:
        data = np.loadtxt(file_path, delimiter=',', skiprows=1)
        data = np.atleast_2d(data)
        if data.size == 0:
            return None, None

        r = data[:, 0]
        phi = data[:, 1]
        z = data[:, 2]
        elevation = np.degrees(np.arctan2(z, r))
        azimuth = phi
        return azimuth, elevation
    except Exception as exc:
        logger.error(f"Error loading measurement positions: {exc}")
        return None, None


def update_measurement_position_plot(plot_widget=None):
    plot = plot_widget or measurement_position_plot
    if plot is None:
        return

    azimuth, elevation = load_measurement_data()
    figure = plot.figure
    figure.clear()
    figure.set_layout_engine('constrained')
    ax = figure.add_subplot(111)

    if azimuth is not None and elevation is not None:
        scatter = ax.scatter(
            azimuth,
            elevation,
            c=elevation,
            cmap='viridis',
            marker='o',
            s=20,
        )
        ax.set_title('Measurement Positions (Azimuth vs Elevation)')
        figure.colorbar(scatter, ax=ax, label='Elevation (degrees)')
    else:
        ax.text(
            0,
            0,
            'No measurement positions available',
            horizontalalignment='center',
            verticalalignment='center',
        )
        ax.set_title('Waiting for measurement positions...')

    ax.set_xlabel('Azimuth (degrees)')
    ax.set_ylabel('Elevation (degrees)')
    ax.set_xlim(-180, 180)
    ax.set_ylim(-90, 90)
    ax.grid(True, alpha=0.3)
    plot.update()


def update_impulse_response_plot(plot_widget=None):
    plot = plot_widget or impulse_response_plot
    if plot is None:
        return

    latest_file, ir, fs = _load_latest_ir()
    figure = plot.figure
    figure.clear()
    figure.set_layout_engine('constrained')
    ax = figure.add_subplot(111)

    if latest_file is None or ir is None or fs is None:
        ax.text(0, 0, 'No impulse response available',
                horizontalalignment='center', verticalalignment='center')
        ax.set_title('Waiting for impulse response...')
    else:
        zoom_ms = 15.0
        zoom_samples = int((zoom_ms / 1000.0) * fs)
        peak_idx = np.argmax(np.abs(ir))
        start_idx = max(0, peak_idx - int(zoom_samples / 4))
        end_idx = start_idx + zoom_samples
        ir_zoom = ir[start_idx:end_idx]
        time_axis = (np.arange(len(ir_zoom)) / fs) * 1000.0
        ax.plot(time_axis, ir_zoom)
        ax.set_title(f'Impulse Response (Zoomed): {latest_file.name}')

    ax.set_xlabel('Time (ms)')
    ax.set_ylabel('Amplitude')
    ax.grid(True, alpha=0.3)
    plot.update()


def update_frequency_response_plot(plot_widget=None):
    plot = plot_widget or frequency_response_plot
    if plot is None:
        return

    latest_file, ir, fs = _load_latest_ir()
    figure = plot.figure
    figure.clear()
    figure.set_layout_engine('constrained')
    ax = figure.add_subplot(111)

    if latest_file is None or ir is None or fs is None:
        ax.text(0, 0, 'No frequency response available',
                horizontalalignment='center', verticalalignment='center')
        ax.set_title('Waiting for frequency response...')
    else:
        n_fft = 2 ** int(np.ceil(np.log2(len(ir))))
        fr = np.fft.rfft(ir, n=n_fft)
        freqs = np.fft.rfftfreq(n_fft, d=1 / fs)
        mag_db = 20 * np.log10(np.abs(fr) + 1e-12)
        ax.semilogx(freqs, mag_db)
        ax.set_title(f'Frequency Response: {latest_file.name}')
        ax.set_xlim(20, 20000)
        ax.set_ylim(-60, 10)

    ax.set_xlabel('Frequency (Hz)')
    ax.set_ylabel('Magnitude (dB)')
    ax.grid(True, which='both', alpha=0.3)
    plot.update()


def update_live_capture_plots():
    update_measurement_position_plot()
    update_impulse_response_plot()
    update_frequency_response_plot()


async def watch_measurement_points():
    measurement_dir = Path('./measurements')
    last_mtime = 0
    last_file_path = None

    while True:
        try:
            all_csv_files = []
            if measurement_dir.exists():
                all_csv_files.extend(measurement_dir.glob('*/measurement_points.csv'))

            root_csv = Path('measurement_points.csv')
            if root_csv.exists():
                all_csv_files.append(root_csv)
            root_pos_csv = Path('measurement_positions.csv')
            if root_pos_csv.exists():
                all_csv_files.append(root_pos_csv)

            if all_csv_files:
                current_file_path = max(
                    all_csv_files,
                    key=lambda file: file.stat().st_mtime,
                )
                current_mtime = current_file_path.stat().st_mtime
                if current_mtime != last_mtime \
                        or current_file_path != last_file_path:
                    last_mtime = current_mtime
                    last_file_path = current_file_path
                    update_measurement_position_plot()

            await asyncio.sleep(1)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Error watching measurement positions: {exc}")
            await asyncio.sleep(1)


async def watch_ir_files():
    last_ir_mtime = 0

    while True:
        try:
            latest_file = _find_latest_ir_file()
            latest_ir_mtime = latest_file.stat().st_mtime if latest_file else 0

            if latest_ir_mtime > last_ir_mtime:
                last_ir_mtime = latest_ir_mtime
                update_impulse_response_plot()
                update_frequency_response_plot()

            await asyncio.sleep(1)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Error watching IR files: {exc}")
            await asyncio.sleep(1)


def _build_plot_panel(title, update_callback):
    state = {'expanded': False}

    with ui.element('div').classes(
        'w-full border border-gray-300 rounded bg-white p-2'
    ) as panel:
        with ui.row().classes('w-full items-center justify-between gap-2'):
            ui.label(title).classes('text-sm font-bold text-gray-700')
            expand_button = ui.button(icon='open_in_full').props('flat round dense')

        with ui.column().classes('w-full') as plot_container:
            plot_widget = ui.matplotlib(figsize=(16, 4)).classes('w-full')

        def apply_state():
            expand_button.props(
                f'icon={"close_fullscreen" if state["expanded"] else "open_in_full"}'
            )
            height = '620px' if state['expanded'] else '300px'
            plot_widget.style(f'height: {height};')
            plot_widget.figure.set_size_inches(16, 7 if state['expanded'] else 4)
            update_callback(plot_widget)

        def toggle_expand():
            state['expanded'] = not state['expanded']
            apply_state()

        expand_button.on('click', toggle_expand)
        apply_state()
        return panel, plot_widget


def build_live_capture():
    """Build the live capture monitoring panel."""
    global measurement_position_plot, impulse_response_plot, frequency_response_plot

    with ui.column().classes('w-full h-full min-w-0 flex flex-col p-2 gap-2 overflow-auto'):
        with ui.row().classes('w-full items-center gap-3 flex-wrap') as header_bar:
            ui.label('Live Capture').classes('text-xl font-bold')

        measurement_position_panel, measurement_position_plot = _build_plot_panel(
            'Measurement Positions',
            update_measurement_position_plot,
        )
        frequency_response_panel, frequency_response_plot = _build_plot_panel(
            'Frequency Response',
            update_frequency_response_plot,
        )
        impulse_response_panel, impulse_response_plot = _build_plot_panel(
            'Impulse Response',
            update_impulse_response_plot,
        )

        panel_map = {
            'Measurement Positions': measurement_position_panel,
            'Frequency Response': frequency_response_panel,
            'Impulse Response': impulse_response_panel,
        }

        def set_plot_button_state(button, is_visible):
            if is_visible:
                button.props('color=green')
                button.classes(replace='text-white border border-green-700 font-bold')
                button.style('')
            else:
                button.props(remove='color')
                button.classes(replace='text-white font-bold')
                button.style(
                    'background: rgb(99, 154, 210); '
                    'border: 1px solid rgb(68, 111, 154);'
                )

        with header_bar:
            for label, panel in panel_map.items():
                state = {'visible': True}
                button = ui.button(label).props(
                    'dense unelevated rounded color=green'
                ).classes('text-white border border-green-700 font-bold')

                def toggle_plot(p=panel, b=button, s=state):
                    s['visible'] = not s['visible']
                    p.set_visibility(s['visible'])
                    set_plot_button_state(b, s['visible'])

                button.on('click', toggle_plot)

    ui.timer(0, watch_measurement_points, once=True)
    ui.timer(0, watch_ir_files, once=True)
    update_live_capture_plots()
