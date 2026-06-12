import asyncio
import configparser
import json
import math
import time
import warnings
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import soundfile as sf
from loguru import logger
from nicegui import ui

from grid_generator.coord_viewer_core import CoordViewerEngine
from harmonic_drive import project
from nfs.audio import get_audio_meter_state


ui.add_css("""
.live-capture-plotly .nsewdrag,
.live-capture-plotly .drag,
.live-capture-plotly .cursor-crosshair {
  cursor: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24'%3E%3Cpath d='M12 2v20M2 12h20' stroke='white' stroke-width='4' stroke-linecap='square'/%3E%3Cpath d='M12 2v20M2 12h20' stroke='black' stroke-width='2' stroke-linecap='square'/%3E%3C/svg%3E") 12 12, crosshair !important;
}
.live-capture-drag-handle {
  cursor: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='28' height='28' viewBox='0 0 28 28'%3E%3Ctext x='14' y='21' text-anchor='middle' font-size='21' font-family='Arial, sans-serif' font-weight='700' fill='white' stroke='black' stroke-width='3' paint-order='stroke fill'%3E%E2%9C%8B%3C/text%3E%3C/svg%3E") 14 14, grab !important;
}
.live-capture-drag-handle:active {
  cursor: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='28' height='28' viewBox='0 0 28 28'%3E%3Ctext x='14' y='21' text-anchor='middle' font-size='21' font-family='Arial, sans-serif' font-weight='700' fill='white' stroke='black' stroke-width='3' paint-order='stroke fill'%3E%E2%9C%8A%3C/text%3E%3C/svg%3E") 14 14, grabbing !important;
}
.live-capture-collapsible.live-capture-collapsed {
  min-height: 40px;
  height: 40px;
  padding-top: 3px;
  padding-bottom: 3px;
}
.live-capture-collapsible.live-capture-collapsed > :not(:first-child) {
  display: none !important;
}
""", shared=True)


grid_progress_engine = None
grid_progress_title_label = None
grid_progress_grid_path = None
grid_progress_grid_mtime = None
measurement_position_plot = None
impulse_response_plot = None
frequency_response_plot = None
frequency_smoothing_input = None
frequency_smoothing_fraction = 24
live_capture_config_file = 'config.ini'
live_capture_started_at = time.time()
preview_ir_data = None

GRID_FILE_CANDIDATES = (
    Path('grid1.csv'),
    Path('scan_path.csv'),
    Path('MySpeaker_scan_path.csv'),
    Path('jan_cylinder_grid1.csv'),
)

LIVE_CAPTURE_CONFIG_SECTION = 'live_capture'
PANEL_ORDER_CONFIG_KEY = 'panel_order'
VISIBLE_PANELS_CONFIG_KEY = 'visible_panels'
FREQUENCY_SMOOTHING_CONFIG_KEY = 'frequency_smoothing_fraction'
DEFAULT_FREQUENCY_SMOOTHING_FRACTION = 24
FREQUENCY_SMOOTHING_OPTIONS = {
    0: 'Off',
    3: '1/3',
    6: '1/6',
    12: '1/12',
    24: '1/24',
    48: '1/48',
}
PANEL_LABELS = [
    'Audio Meters',
    '3D Progress',
    'Measurement Positions',
    'Frequency Response',
    'Impulse Response',
]
DEFAULT_VISIBLE_PANELS = [
    '3D Progress',
    'Measurement Positions',
]


def _find_latest_ir_file():
    session_started_at = live_capture_started_at
    root = project.get_project_dir()
    search_dirs = [
        root / 'measurement_set',
        root / 'single_measurements',
    ]
    search_dirs.append(Path('./Recordings'))

    wav_files = []
    for directory in search_dirs:
        if directory.exists():
            wav_files.extend(
                file
                for file in directory.glob('*_ir.wav')
                if file.stat().st_mtime >= session_started_at
            )

    if not wav_files:
        return None
    return max(wav_files, key=lambda file: file.stat().st_mtime)


def reset_live_capture_session() -> None:
    """Clear live-only plots by ignoring captures made before this moment."""
    global live_capture_started_at, preview_ir_data
    live_capture_started_at = time.time()
    preview_ir_data = None
    update_impulse_response_plot()
    update_frequency_response_plot()


def _load_latest_ir():
    if preview_ir_data is not None:
        return preview_ir_data["path"], preview_ir_data["ir"], preview_ir_data["fs"]

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


def set_preview_ir(result) -> None:
    """Show a transient test-sweep IR without saving it to disk."""
    global preview_ir_data
    if not result:
        return
    preview_ir_data = {
        "path": Path(str(result.get("name", "Test Sweep"))),
        "ir": result.get("ir_linear"),
        "fs": result.get("fs"),
    }
    update_impulse_response_plot()
    update_frequency_response_plot()


def _find_latest_measurement_positions_file():
    root = project.get_project_dir()
    all_csv_files = []

    root_pos_csv = root / 'measurement_positions.csv'
    if root_pos_csv.exists():
        all_csv_files.append(root_pos_csv)

    if not all_csv_files:
        return None
    return max(all_csv_files, key=lambda file: file.stat().st_mtime)


def _count_measurement_rows(file_path):
    if file_path is None or not file_path.exists():
        return 0
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return max(0, sum(1 for _line in f) - 1)
    except OSError as exc:
        logger.error(f"Error counting measurement positions: {exc}")
        return 0


def _find_grid_file():
    root = project.get_project_dir()
    configured = []
    grid_vars = project.get_project_data().get("grid_vars", {})
    if isinstance(grid_vars, dict) and grid_vars.get("output_filename"):
        configured.append(Path(str(grid_vars["output_filename"])))
    configured.append(Path(project.get_grid_filename()))
    candidates = [*configured, *GRID_FILE_CANDIDATES]
    existing = [root / path for path in candidates if (root / path).exists()]
    existing.extend(path for path in candidates if path.exists())
    if not existing:
        return None
    return max(existing, key=lambda file: file.stat().st_mtime)


def load_measurement_data():
    file_path = _find_latest_measurement_positions_file()
    if file_path is None:
        return None, None

    try:
        if _count_measurement_rows(file_path) == 0:
            return None, None

        with warnings.catch_warnings():
            warnings.simplefilter('error', UserWarning)
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
    figure = _new_live_capture_figure()
    if azimuth is not None and elevation is not None:
        figure.add_trace(
            go.Scatter(
                x=azimuth,
                y=elevation,
                mode='markers',
                marker=dict(
                    size=8,
                    color=elevation,
                    colorscale='Viridis',
                    colorbar=dict(title='Elevation (degrees)'),
                    line=dict(width=0),
                ),
                hovertemplate='Azimuth: %{x:.1f} deg<br>'
                              'Elevation: %{y:.1f} deg<extra></extra>',
            )
        )
        title = 'Measurement Positions (Azimuth vs Elevation)'
    else:
        figure.add_annotation(
            text='No measurement positions available',
            x=0.5,
            y=0.5,
            xref='paper',
            yref='paper',
            showarrow=False,
        )
        title = 'Waiting for measurement positions...'

    figure.update_layout(
        title=title,
        xaxis=dict(
            title='Azimuth (degrees)',
            range=[-180, 180],
            tickmode='array',
            tickvals=[-180, -120, -60, 0, 60, 120, 180],
        ),
        yaxis=dict(
            title='Elevation (degrees)',
            range=[-90, 90],
            tickmode='array',
            tickvals=[-90, -60, -30, 0, 30, 60, 90],
        ),
    )
    plot.update_figure(figure)


def update_impulse_response_plot(plot_widget=None):
    plot = plot_widget or impulse_response_plot
    if plot is None:
        return

    latest_file, ir, fs = _load_latest_ir()
    figure = _new_live_capture_figure()
    if latest_file is None or ir is None or fs is None:
        figure.add_annotation(
            text='No impulse response available',
            x=0.5,
            y=0.5,
            xref='paper',
            yref='paper',
            showarrow=False,
        )
        title = 'Waiting for impulse response...'
    else:
        zoom_ms = 15.0
        zoom_samples = int((zoom_ms / 1000.0) * fs)
        peak_idx = np.argmax(np.abs(ir))
        start_idx = max(0, peak_idx - int(zoom_samples / 4))
        end_idx = start_idx + zoom_samples
        ir_zoom = ir[start_idx:end_idx]
        time_axis = (np.arange(len(ir_zoom)) / fs) * 1000.0
        figure.add_trace(
            go.Scatter(
                x=time_axis,
                y=ir_zoom,
                mode='lines',
                line=dict(color='#1f77b4', width=2),
                hovertemplate='Time: %{x:.3f} ms<br>'
                              'Amplitude: %{y:.4g}<extra></extra>',
            )
        )
        title = f'Impulse Response (Zoomed): {latest_file.name}'

    figure.update_layout(
        title=title,
        xaxis=dict(title='Time (ms)'),
        yaxis=dict(title='Amplitude'),
    )
    plot.update_figure(figure)


def update_frequency_response_plot(plot_widget=None):
    plot = plot_widget or frequency_response_plot
    if plot is None:
        return

    latest_file, ir, fs = _load_latest_ir()
    figure = _new_live_capture_figure()
    if latest_file is None or ir is None or fs is None:
        figure.add_annotation(
            text='No frequency response available',
            x=0.5,
            y=0.5,
            xref='paper',
            yref='paper',
            showarrow=False,
        )
        title = 'Waiting for frequency response...'
    else:
        n_fft = 2 ** int(np.ceil(np.log2(len(ir))))
        fr = np.fft.rfft(ir, n=n_fft)
        freqs = np.fft.rfftfreq(n_fft, d=1 / fs)
        mag_db = 20 * np.log10(np.abs(fr) + 1e-12)
        valid = freqs > 0
        smoothed_mag_db = _smooth_fractional_octave(
            freqs[valid],
            mag_db[valid],
            frequency_smoothing_fraction,
        )
        figure.add_trace(
            go.Scatter(
                x=freqs[valid],
                y=smoothed_mag_db,
                mode='lines',
                line=dict(color='#1f77b4', width=2),
                hovertemplate='Frequency: %{x:.0f} Hz<br>'
                              'Magnitude: %{y:.2f} dB<extra></extra>',
            )
        )
        if frequency_smoothing_fraction > 0:
            title = (
                f'Frequency Response '
                f'(1/{frequency_smoothing_fraction} Oct Smoothed): '
                f'{latest_file.name}'
            )
        else:
            title = f'Frequency Response: {latest_file.name}'

    audio_ticks = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
    figure.update_layout(
        title=title,
        xaxis=dict(
            title='Frequency (Hz)',
            type='log',
            range=[np.log10(20), np.log10(20000)],
            tickmode='array',
            tickvals=audio_ticks,
            ticktext=[_format_audio_frequency_tick(value) for value in audio_ticks],
        ),
        yaxis=dict(title='Magnitude (dB)', range=[-60, 10]),
    )
    plot.update_figure(figure)


def _smooth_fractional_octave(freqs, mag_db, fraction):
    if len(freqs) == 0 or fraction <= 0:
        return mag_db

    half_width = 2 ** (1 / (2 * fraction))
    linear_mag = 10 ** (mag_db / 20)
    low_indices = np.searchsorted(freqs, freqs / half_width, side='left')
    high_indices = np.searchsorted(freqs, freqs * half_width, side='right')
    cumulative = np.concatenate(([0.0], np.cumsum(linear_mag)))
    window_sums = cumulative[high_indices] - cumulative[low_indices]
    window_counts = np.maximum(1, high_indices - low_indices)
    return 20 * np.log10((window_sums / window_counts) + 1e-12)


def _format_audio_frequency_tick(value):
    if value <= 0:
        return ''
    if value >= 1000:
        value_khz = value / 1000
        return f'{value_khz:g}k'
    return f'{value:g}'


def _new_live_capture_figure():
    figure = go.Figure()
    figure.update_layout(
        template='plotly_white',
        margin=dict(l=58, r=24, t=48, b=52),
        hovermode='closest',
        showlegend=False,
        xaxis=dict(showgrid=True, gridcolor='rgba(0,0,0,0.16)'),
        yaxis=dict(showgrid=True, gridcolor='rgba(0,0,0,0.16)'),
    )
    return figure


def _meter_bar_value(db_value):
    return round(max(0.0, min(1.0, (float(db_value) + 60.0) / 60.0)), 3)


def _meter_bar_percent(db_value):
    return _meter_bar_value(db_value) * 100.0


def _format_meter_db(db_value):
    db_value = float(db_value)
    if db_value <= -119.0:
        return '-inf'
    return f'{db_value:.1f}'


def _average_dbfs(db_values):
    powers = [10 ** (float(value) / 10.0) for value in db_values if float(value) > -119.0]
    if not powers:
        return -120.0
    return 10.0 * math.log10(sum(powers) / len(powers))


def _set_panel_collapsed(panel, button, collapsed):
    if collapsed:
        panel.classes(add='live-capture-collapsed')
        button.set_icon('expand_more')
    else:
        panel.classes(remove='live-capture-collapsed')
        button.set_icon('expand_less')
    button.update()


def _set_plot_panel_collapsed(panel, collapse_button, expand_button, collapsed):
    _set_panel_collapsed(panel, collapse_button, collapsed)
    expand_button.set_visibility(not collapsed)


def _load_audio_channel_labels(config_file):
    parser = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    parser.read(config_file)

    def channel(key, fallback):
        try:
            return parser.getint('audio', key, fallback=fallback)
        except ValueError:
            return fallback

    return [
        f"Mic Input CH {channel('in_ch_mic', 1)}",
        f"Speaker Output CH {channel('out_ch_spkr', 0)}",
        f"Loopback Input CH {channel('in_ch_loop', 0)}",
        f"Loopback Output CH {channel('out_ch_ref', 1)}",
    ]


def _build_audio_meter_tile(label, kind):
    tone_classes = (
        'h-[58px] border border-pink-200 rounded bg-pink-50/60 px-2 py-1 min-w-0'
        if kind == 'input'
        else 'h-[58px] border border-blue-200 rounded bg-blue-50/60 px-2 py-1 min-w-0'
    )
    with ui.element('div').classes(
        tone_classes
    ):
        with ui.column().classes('w-full h-full gap-0 min-w-0'):
            label_el = ui.label(label).classes(
                'w-full text-xs font-bold text-gray-700 truncate'
            )
            with ui.row().classes('w-full items-center gap-2 flex-nowrap min-w-0'):
                with ui.column().classes('flex-1 min-w-32 gap-0'):
                    with ui.element('div').classes(
                        'relative h-[18px] w-full overflow-hidden rounded bg-gray-200'
                    ):
                        bar_fill = ui.element('div').classes(
                            'absolute left-0 top-0 h-full bg-blue-500'
                        ).style('width: 0%')
                        peak_marker = ui.element('div').classes(
                            'absolute top-0 h-full w-[2px] bg-red-600'
                        ).style('left: 0%')
                    with ui.row().classes('w-full justify-between text-[10px] leading-none text-gray-500'):
                        ui.label('-60 dB')
                        ui.label('0 dB')
                with ui.column().classes('w-16 shrink-0 gap-0 text-xs text-gray-600 leading-tight'):
                    rms = ui.label('RMS -inf').classes('whitespace-nowrap')
                    ui.label('dBFS').classes('text-[10px] text-gray-500')
                with ui.column().classes('w-16 shrink-0 gap-0 text-xs text-gray-600 leading-tight'):
                    peak = ui.label('Peak -inf').classes('whitespace-nowrap')
                    ui.label('dBFS').classes('text-[10px] text-gray-500')
    return {
        'label': label_el,
        'bar_fill': bar_fill,
        'peak_marker': peak_marker,
        'rms': rms,
        'peak': peak,
    }


def _build_audio_meters_panel(config_file):
    with ui.element('div').classes(
        'live-capture-collapsible w-full shrink-0 border border-gray-300 rounded bg-white p-2'
    ) as panel:
        with ui.row().classes('w-full items-center justify-between gap-2 mb-1'):
            with ui.row().classes('items-center gap-1 min-w-0'):
                ui.icon('drag_indicator').classes(
                    'live-capture-drag-handle text-gray-500'
                )
                ui.label('Audio Meters').classes('text-sm font-bold text-gray-700')
            collapse_button = ui.button(icon='expand_less').props('flat round dense')

        labels = _load_audio_channel_labels(config_file)
        with ui.element('div').classes('grid grid-cols-2 gap-2 w-full') as grid:
            rows = [
                _build_audio_meter_tile(labels[0], 'input'),
                _build_audio_meter_tile(labels[1], 'output'),
                _build_audio_meter_tile(labels[2], 'input'),
                _build_audio_meter_tile(labels[3], 'output'),
            ]
        meter_windows = [{'rms': [], 'peak': []} for _ in rows]
        peak_marker_db = [-120.0 for _ in rows]
        last_text_update = time.monotonic()
        last_meter_update = time.monotonic()

        def refresh():
            nonlocal last_text_update, last_meter_update
            now = time.monotonic()
            elapsed = max(0.0, now - last_meter_update)
            last_meter_update = now
            labels_now = _load_audio_channel_labels(config_file)
            state = get_audio_meter_state()
            meters = [
                state['inputs'][1],
                state['outputs'][0],
                state['inputs'][0],
                state['outputs'][1],
            ]
            update_text = now - last_text_update >= 1.0
            for index, (row, label, meter, window) in enumerate(zip(rows, labels_now, meters, meter_windows)):
                rms = meter.get('rms_dbfs', -120.0)
                peak = meter.get('peak_dbfs', -120.0)
                row['label'].set_text(label)
                row['bar_fill'].style(f'width: {_meter_bar_percent(rms):.1f}%')
                if peak >= peak_marker_db[index]:
                    peak_marker_db[index] = float(peak)
                else:
                    peak_marker_db[index] = max(
                        float(peak),
                        peak_marker_db[index] - (12.0 * elapsed),
                    )
                row['peak_marker'].style(
                    f'left: calc({_meter_bar_percent(peak_marker_db[index]):.1f}% - 1px)'
                )
                window['rms'].append(rms)
                window['peak'].append(peak)
                if update_text:
                    row['rms'].set_text(
                        f"RMS {_format_meter_db(_average_dbfs(window['rms']))}"
                    )
                    row['peak'].set_text(
                        f"Peak {_format_meter_db(max(window['peak']))}"
                    )
                    window['rms'].clear()
                    window['peak'].clear()
                if meter.get('clip'):
                    row['peak'].classes(add='text-red-600 font-bold')
                else:
                    row['peak'].classes(remove='text-red-600 font-bold')
            if update_text:
                last_text_update = time.monotonic()

        ui.timer(0.1, refresh)
        refresh()
        return panel, collapse_button


def update_live_capture_plots():
    _refresh_frequency_smoothing_from_config()
    update_grid_progress_viewer()
    update_measurement_position_plot()
    update_impulse_response_plot()
    update_frequency_response_plot()


def _load_frequency_smoothing_fraction(parser):
    smoothing_fraction = DEFAULT_FREQUENCY_SMOOTHING_FRACTION
    if parser.has_option(
        LIVE_CAPTURE_CONFIG_SECTION,
        FREQUENCY_SMOOTHING_CONFIG_KEY,
    ):
        try:
            smoothing_fraction = parser.getint(
                LIVE_CAPTURE_CONFIG_SECTION,
                FREQUENCY_SMOOTHING_CONFIG_KEY,
            )
        except ValueError:
            logger.warning(
                f"Invalid {FREQUENCY_SMOOTHING_CONFIG_KEY}; "
                f"using {DEFAULT_FREQUENCY_SMOOTHING_FRACTION}"
            )
    return max(0, smoothing_fraction)


def _load_panel_layout(config_file):
    parser = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    parser.read(config_file)
    configured_order = []
    if parser.has_option(LIVE_CAPTURE_CONFIG_SECTION, PANEL_ORDER_CONFIG_KEY):
        configured_order = [
            item.strip()
            for item in parser.get(LIVE_CAPTURE_CONFIG_SECTION, PANEL_ORDER_CONFIG_KEY).split(',')
            if item.strip()
        ]

    order = [label for label in configured_order if label in PANEL_LABELS]
    order.extend(label for label in PANEL_LABELS if label not in order)

    configured_visible = []
    if parser.has_option(LIVE_CAPTURE_CONFIG_SECTION, VISIBLE_PANELS_CONFIG_KEY):
        configured_visible = [
            item.strip()
            for item in parser.get(LIVE_CAPTURE_CONFIG_SECTION, VISIBLE_PANELS_CONFIG_KEY).split(',')
            if item.strip()
        ]
    else:
        configured_visible = DEFAULT_VISIBLE_PANELS

    visible = {label for label in configured_visible if label in PANEL_LABELS}
    smoothing_fraction = _load_frequency_smoothing_fraction(parser)

    return order, visible, max(0, smoothing_fraction)


def _refresh_frequency_smoothing_from_config():
    global frequency_smoothing_fraction
    parser = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    parser.read(live_capture_config_file)
    frequency_smoothing_fraction = _load_frequency_smoothing_fraction(parser)
    if frequency_smoothing_input is not None:
        smoothing_value = (
            frequency_smoothing_fraction
            if frequency_smoothing_fraction in FREQUENCY_SMOOTHING_OPTIONS
            else DEFAULT_FREQUENCY_SMOOTHING_FRACTION
        )
        frequency_smoothing_input.set_value(smoothing_value)


def _save_panel_layout(
    config_file,
    panel_order,
    visible_panels,
    smoothing_fraction,
):
    parser = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    parser.read(config_file)
    if not parser.has_section(LIVE_CAPTURE_CONFIG_SECTION):
        parser.add_section(LIVE_CAPTURE_CONFIG_SECTION)
    parser.set(
        LIVE_CAPTURE_CONFIG_SECTION,
        PANEL_ORDER_CONFIG_KEY,
        ', '.join(label for label in panel_order if label in PANEL_LABELS),
    )
    parser.set(
        LIVE_CAPTURE_CONFIG_SECTION,
        VISIBLE_PANELS_CONFIG_KEY,
        ', '.join(label for label in panel_order if label in visible_panels),
    )
    parser.set(
        LIVE_CAPTURE_CONFIG_SECTION,
        FREQUENCY_SMOOTHING_CONFIG_KEY,
        str(max(0, int(smoothing_fraction))),
    )
    with open(config_file, 'w', encoding='utf-8') as f:
        parser.write(f)


def update_grid_progress_viewer():
    global grid_progress_grid_path, grid_progress_grid_mtime
    if grid_progress_engine is None:
        return

    grid_file = _find_grid_file()
    grid_path = str(grid_file.resolve()) if grid_file is not None else None
    grid_mtime = grid_file.stat().st_mtime if grid_file is not None else None

    if grid_path != grid_progress_grid_path or grid_mtime != grid_progress_grid_mtime:
        if grid_file is None:
            return
        try:
            grid_progress_engine.load_data(str(grid_file))
            grid_progress_grid_path = grid_path
            grid_progress_grid_mtime = grid_mtime
            if grid_progress_title_label is not None:
                grid_progress_title_label.set_text(
                    f'Measurement Progress: {grid_file.name}'
                )
        except Exception as exc:
            logger.error(f"Error reloading grid progress file: {exc}")
            return

    measurement_file = _find_latest_measurement_positions_file()
    measured_count = _count_measurement_rows(measurement_file)
    if grid_progress_engine.N <= 0:
        return

    current_idx = max(0, min(measured_count - 1, grid_progress_engine.N - 1))
    grid_progress_engine.set_current_index(current_idx)


async def watch_measurement_points():
    last_mtime = 0
    last_file_path = None

    while True:
        try:
            try:
                from harmonic_drive import control
                control.refresh_measurement_progress()
            except Exception as exc:
                logger.debug(f"Measurement progress refresh skipped: {exc}")

            all_csv_files = []
            root_pos_csv = project.get_project_dir() / 'measurement_positions.csv'
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
                    update_grid_progress_viewer()
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
        'live-capture-collapsible w-full shrink-0 border border-gray-300 rounded bg-white p-2'
    ) as panel:
        with ui.row().classes('w-full items-center justify-between gap-2'):
            with ui.row().classes('items-center gap-1 min-w-0'):
                ui.icon('drag_indicator').classes(
                    'live-capture-drag-handle text-gray-500'
                )
                ui.label(title).classes('text-sm font-bold text-gray-700')
            with ui.row().classes('items-center gap-1'):
                expand_button = ui.button(icon='open_in_full').props('flat round dense')
                collapse_button = ui.button(icon='expand_less').props('flat round dense')

        with ui.column().classes('w-full') as plot_container:
            plot_widget = ui.plotly(_new_live_capture_figure()).classes(
                'w-full live-capture-plotly'
            )

        def apply_state():
            expand_button.props(
                f'icon={"close_fullscreen" if state["expanded"] else "open_in_full"}'
            )
            height = '620px' if state['expanded'] else '300px'
            plot_widget.style(f'height: {height};')
            update_callback(plot_widget)

        def toggle_expand():
            state['expanded'] = not state['expanded']
            apply_state()

        expand_button.on('click', toggle_expand)
        apply_state()
        return panel, plot_widget, collapse_button, expand_button


def _build_grid_progress_panel():
    global grid_progress_title_label, grid_progress_grid_path, grid_progress_grid_mtime
    grid_file = _find_grid_file()
    grid_progress_grid_path = str(grid_file.resolve()) if grid_file is not None else None
    grid_progress_grid_mtime = grid_file.stat().st_mtime if grid_file is not None else None

    with ui.element('div').classes(
        'live-capture-collapsible w-full shrink-0 border border-gray-300 rounded bg-white p-2'
    ) as panel:
        with ui.row().classes('w-full items-center justify-between gap-2'):
            title = 'Measurement Progress'
            if grid_file is not None:
                title = f'{title}: {grid_file.name}'
            with ui.row().classes('items-center gap-1 min-w-0'):
                ui.icon('drag_indicator').classes(
                    'live-capture-drag-handle text-gray-500'
                )
                grid_progress_title_label = ui.label(title).classes(
                    'text-sm font-bold text-gray-700'
                )
            collapse_button = ui.button(icon='expand_less').props('flat round dense')

        if grid_file is None:
            ui.label('No planned grid file found. Generate a grid first.').classes(
                'text-sm text-gray-600 p-4'
            )
            return panel, None, collapse_button

        with ui.row().classes('w-full items-center justify-between flex-wrap gap-2 mb-2'):
            with ui.row().classes('items-center gap-1'):
                ui.button('Top', on_click=lambda: engine.set_view(90, 0)).props(
                    'outline size=sm padding="xs sm"'
                )
                ui.button('Front', on_click=lambda: engine.set_view(0, -90)).props(
                    'outline size=sm padding="xs sm"'
                )
                ui.button('Side', on_click=lambda: engine.set_view(0, 0)).props(
                    'outline size=sm padding="xs sm"'
                )

                ui.checkbox(
                    'Ortho',
                    value=False,
                    on_change=lambda e: engine.set_ortho(e.value),
                ).props('dense size=sm').classes('text-sm text-gray-700 ml-1')
                ui.checkbox(
                    'Bounds',
                    value=True,
                    on_change=lambda e: engine.set_bounds_visibility(e.value),
                ).props('dense size=sm').classes('text-sm text-gray-700 ml-1')

            with ui.row().classes('items-center gap-1'):
                ui.label('Rot:').classes('text-sm font-semibold text-gray-700')
                rot_ang_input = ui.number(value=45, step=5).props(
                    'dense outlined bg-color=white size=sm'
                ).classes('w-14')

                def toggle_rotate():
                    if engine.is_rotating:
                        engine.stop_rotation()
                        rot_btn.props('outline')
                    else:
                        engine.start_rotation(rot_ang_input.value)
                        rot_btn.props(remove='outline')

                rot_btn = ui.button('Rotate', on_click=toggle_rotate).props(
                    'outline color=primary size=sm padding="xs sm"'
                )

                with ui.button('More', icon='tune').props(
                    'outline color=primary size=sm padding="xs sm"'
                ):
                    with ui.menu().classes('p-4 flex flex-col gap-3'):
                        ui.label('Display Settings').classes(
                            'text-sm font-bold text-gray-800 border-b pb-1 mb-1'
                        )
                        with ui.row().classes('items-center gap-2'):
                            ui.label('Tail Len:').classes(
                                'text-sm font-semibold text-gray-700 w-16'
                            )
                            ui.slider(
                                min=10,
                                max=500,
                                value=50,
                                on_change=lambda e: engine.set_tail_length(e.value),
                            ).props('dense').classes('w-24')
                        ui.checkbox(
                            'Fade History',
                            value=False,
                            on_change=lambda e: engine.set_history_mode(e.value),
                        ).props('dense size=sm').classes('text-sm text-gray-700')
                        with ui.row().classes('items-center gap-2'):
                            ui.label('Rot Speed:').classes(
                                'text-sm font-semibold text-gray-700 w-16'
                            )
                            ui.number(
                                value=5,
                                min=1,
                                max=180,
                                step=1,
                                on_change=lambda e: engine.set_rotation_speed(e.value),
                            ).props(
                                'dense outlined bg-color=white size=sm suffix="deg/s"'
                            ).classes('w-28')
                        with ui.row().classes('items-center gap-2'):
                            ui.label('Color:').classes(
                                'text-sm font-semibold text-gray-700 w-16'
                            )
                            ui.slider(
                                min=0.0,
                                max=1.0,
                                step=0.01,
                                value=0.5,
                                on_change=lambda e: engine.set_color(e.value),
                            ).props('dense').classes('w-24')
                        with ui.row().classes('items-center gap-2'):
                            ui.label('Opacity:').classes(
                                'text-sm font-semibold text-gray-700 w-16'
                            )
                            ui.slider(
                                min=0.1,
                                max=1.0,
                                step=0.05,
                                value=1.0,
                                on_change=lambda e: engine.set_alpha(e.value),
                            ).props('dense').classes('w-24')

        engine = CoordViewerEngine(input_data=str(grid_file))
        engine.set_history_mode(False)
        update_grid_progress_viewer()
        return panel, engine, collapse_button


def _enable_plot_reordering(plot_stack, panel_map):
    panel_ids = {label: panel.id for label, panel in panel_map.items()}
    js = f"""
    function setupLiveCaptureReordering(attempt = 0) {{
        const panelIds = {json.dumps(panel_ids)};
        const stackWrapper = getElement({plot_stack.id});
        const stack = stackWrapper && (stackWrapper.$el || stackWrapper);
        if (!stack) {{
            if (attempt < 20) setTimeout(() => setupLiveCaptureReordering(attempt + 1), 100);
            return;
        }}
        if (stack.dataset.liveCaptureReorderReady === 'true') return;

        stack.dataset.liveCaptureReorderReady = 'true';
        let draggedLabel = null;

        const getPanel = (label) => {{
            const wrapper = getElement(panelIds[label]);
            return wrapper && (wrapper.$el || wrapper);
        }};

        const visibleOrder = () => Object.keys(panelIds).filter((label) => {{
            const panel = getPanel(label);
            return panel && window.getComputedStyle(panel).display !== 'none';
        }}).sort((a, b) => {{
            const aPanel = getPanel(a);
            const bPanel = getPanel(b);
            return Array.prototype.indexOf.call(stack.children, aPanel) -
                Array.prototype.indexOf.call(stack.children, bPanel);
        }});

        const emitOrder = () => {{
            stack.dispatchEvent(new CustomEvent('live-capture-reorder', {{
                detail: {{ order: visibleOrder() }},
                bubbles: true,
            }}));
        }};

        Object.entries(panelIds).forEach(([label, panelId]) => {{
            const panel = getPanel(label);
            if (!panel) return;
            panel.dataset.liveCapturePlotLabel = label;

            const handle = panel.querySelector('.live-capture-drag-handle');
            if (!handle) return;
            handle.setAttribute('draggable', 'true');
            handle.setAttribute('title', 'Drag to reorder');

            handle.addEventListener('dragstart', (event) => {{
                draggedLabel = label;
                panel.classList.add('opacity-60');
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', label);
            }});

            handle.addEventListener('dragend', () => {{
                draggedLabel = null;
                panel.classList.remove('opacity-60');
                Object.keys(panelIds).forEach((targetLabel) => {{
                    const target = getPanel(targetLabel);
                    if (target) target.classList.remove('ring-2', 'ring-blue-400');
                }});
            }});

            panel.addEventListener('dragover', (event) => {{
                if (!draggedLabel || draggedLabel === label) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = 'move';
                panel.classList.add('ring-2', 'ring-blue-400');
            }});

            panel.addEventListener('dragleave', () => {{
                panel.classList.remove('ring-2', 'ring-blue-400');
            }});

            panel.addEventListener('drop', (event) => {{
                if (!draggedLabel || draggedLabel === label) return;
                event.preventDefault();
                panel.classList.remove('ring-2', 'ring-blue-400');

                const draggedPanel = getPanel(draggedLabel);
                const targetPanel = getPanel(label);
                if (!draggedPanel || !targetPanel) return;

                const targetRect = targetPanel.getBoundingClientRect();
                const placeAfter = event.clientY > targetRect.top + targetRect.height / 2;
                stack.insertBefore(
                    draggedPanel,
                    placeAfter ? targetPanel.nextSibling : targetPanel,
                );
                emitOrder();
            }});
        }});
    }}
    setTimeout(() => setupLiveCaptureReordering(), 0);
    """
    ui.run_javascript(js)


def build_live_capture(config_file='config.ini'):
    """Build the live capture monitoring panel."""
    global grid_progress_engine
    global measurement_position_plot, impulse_response_plot, frequency_response_plot
    global frequency_smoothing_input
    global frequency_smoothing_fraction
    global live_capture_config_file
    live_capture_config_file = config_file

    with ui.column().classes(
        'w-full h-full min-h-0 min-w-0 flex flex-col p-2 gap-2 overflow-hidden'
    ):
        with ui.row().classes('w-full shrink-0 items-center gap-3 flex-wrap') as header_bar:
            ui.label('Live Capture').classes('text-xl font-bold')
            with ui.row().classes('items-center gap-1 text-gray-500'):
                ui.icon('drag_indicator').classes('text-sm')
                ui.label('Drag to reorder').classes('text-xs')

        with ui.column().classes(
            'w-full flex-1 min-h-0 min-w-0 gap-2 overflow-y-auto pr-1'
        ) as plot_stack:
            audio_meters_panel, audio_meters_collapse = _build_audio_meters_panel(config_file)
            grid_progress_panel, grid_progress_engine, grid_progress_collapse = _build_grid_progress_panel()
            measurement_position_panel, measurement_position_plot, measurement_position_collapse, measurement_position_expand = _build_plot_panel(
                'Measurement Positions',
                update_measurement_position_plot,
            )
            frequency_response_panel, frequency_response_plot, frequency_response_collapse, frequency_response_expand = _build_plot_panel(
                'Frequency Response',
                update_frequency_response_plot,
            )
            impulse_response_panel, impulse_response_plot, impulse_response_collapse, impulse_response_expand = _build_plot_panel(
                'Impulse Response',
                update_impulse_response_plot,
            )

        panel_map = {
            'Audio Meters': audio_meters_panel,
            '3D Progress': grid_progress_panel,
            'Measurement Positions': measurement_position_panel,
            'Frequency Response': frequency_response_panel,
            'Impulse Response': impulse_response_panel,
        }
        collapse_button_map = {
            'Audio Meters': audio_meters_collapse,
            '3D Progress': grid_progress_collapse,
            'Measurement Positions': measurement_position_collapse,
            'Frequency Response': frequency_response_collapse,
            'Impulse Response': impulse_response_collapse,
        }
        plot_expand_button_map = {
            'Measurement Positions': measurement_position_expand,
            'Frequency Response': frequency_response_expand,
            'Impulse Response': impulse_response_expand,
        }
        panel_order, visible_panels, frequency_smoothing_fraction = \
            _load_panel_layout(config_file)

        def apply_plot_layout():
            for index, label in enumerate(panel_order):
                panel = panel_map[label]
                panel.move(plot_stack, index)
                panel.set_visibility(True)
                collapsed = label not in visible_panels
                if label in plot_expand_button_map:
                    _set_plot_panel_collapsed(
                        panel,
                        collapse_button_map[label],
                        plot_expand_button_map[label],
                        collapsed,
                    )
                else:
                    _set_panel_collapsed(
                        panel,
                        collapse_button_map[label],
                        collapsed,
                    )

        def _event_order(args):
            if isinstance(args, list):
                return args
            if isinstance(args, dict):
                order = args.get('order')
                if isinstance(order, list):
                    return order
            return []

        def sync_visible_order(event):
            requested_order = _event_order(event.args)
            ordered_labels = [
                label for label in requested_order
                if label in panel_map
            ]
            if ordered_labels:
                hidden_labels = [
                    label for label in panel_order
                    if label not in ordered_labels
                ]
                panel_order[:] = ordered_labels + hidden_labels
                apply_plot_layout()

        plot_stack.on(
            'live-capture-reorder',
            sync_visible_order,
            js_handler='event => emit(event.detail.order)',
        )

        def save_current_panel_order_default():
            _save_panel_layout(
                config_file,
                panel_order,
                visible_panels,
                frequency_smoothing_fraction,
            )
            ui.notify('Live Capture settings saved', type='positive')

        def toggle_panel(selected_label):
            if selected_label in visible_panels:
                visible_panels.discard(selected_label)
            else:
                visible_panels.add(selected_label)
            apply_plot_layout()

        for label, button in collapse_button_map.items():
            button.on('click', lambda _e, l=label: toggle_panel(l))

        def set_frequency_smoothing(event):
            global frequency_smoothing_fraction
            value = int(event.value or 0)
            if value not in FREQUENCY_SMOOTHING_OPTIONS:
                value = DEFAULT_FREQUENCY_SMOOTHING_FRACTION
                frequency_smoothing_input.set_value(value)
            frequency_smoothing_fraction = value
            update_frequency_response_plot()

        with header_bar:
            ui.element('div').classes('flex-grow')
            with ui.row().classes('items-center gap-2 mr-6'):
                ui.label('FR Smooth').classes(
                    'text-sm font-semibold text-gray-700'
                )
                frequency_smoothing_input = ui.select(
                    FREQUENCY_SMOOTHING_OPTIONS,
                    value=frequency_smoothing_fraction,
                    on_change=set_frequency_smoothing,
                ).props('dense outlined options-dense bg-color=white').classes('w-24')
                ui.tooltip('Frequency response smoothing')
            with ui.button(
                'Save as Default',
                icon='save',
                on_click=save_current_panel_order_default,
            ).props('flat dense size=sm').classes('text-gray-600'):
                ui.tooltip('Save Live Capture settings')

        apply_plot_layout()
        _enable_plot_reordering(plot_stack, panel_map)

    ui.timer(0, watch_measurement_points, once=True)
    ui.timer(0, watch_ir_files, once=True)
    update_live_capture_plots()
