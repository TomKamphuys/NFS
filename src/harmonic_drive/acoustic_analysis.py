import asyncio
from pathlib import Path

import numpy as np
import soundfile as sf
from loguru import logger
from nicegui import ui


ir_fr_plot = None


def update_ir_fr_plots(ir_plot_container=None):
    """Refresh the impulse-response and frequency-response plots."""
    container = ir_plot_container or ir_fr_plot
    if container is None:
        return

    search_dirs = [Path('./Recordings')]
    measurement_dir = Path('./measurements')
    if measurement_dir.exists():
        search_dirs.extend(measurement_dir.glob('*/Recordings'))

    wav_files = []
    for directory in search_dirs:
        if directory.exists():
            wav_files.extend(list(directory.glob('*_ir.wav')))

    if not wav_files:
        return

    latest_file = max(wav_files, key=lambda f: f.stat().st_mtime)
    try:
        ir, fs = sf.read(str(latest_file))
        if len(ir.shape) > 1:
            ir = ir[:, 0]
    except Exception as exc:
        logger.error(f"Error loading IR: {exc}")
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

    with container:
        figure = container.figure
        figure.clear()
        figure.set_layout_engine('constrained')
        ax1 = figure.add_subplot(2, 1, 1)
        ax1.plot(time_axis, ir_zoom)
        ax1.set_title(f'Impulse Response (Zoomed): {latest_file.name}')
        ax1.set_xlabel('Time (ms)')
        ax1.set_ylabel('Amplitude')
        ax1.grid(True, alpha=0.3)
        ax2 = figure.add_subplot(2, 1, 2)
        ax2.semilogx(freqs, mag_db)
        ax2.set_title(f'Frequency Response: {latest_file.name}')
        ax2.set_xlabel('Frequency (Hz)')
        ax2.set_ylabel('Magnitude (dB)')
        ax2.set_xlim(20, 20000)
        ax2.set_ylim(-60, 10)
        ax2.grid(True, which='both', alpha=0.3)
        container.update()


async def watch_ir_files():
    """Watch measurement recordings and refresh plots when new IRs appear."""
    measurement_dir = Path('./measurements')
    last_ir_mtime = 0

    while True:
        try:
            search_dirs = [Path('./Recordings')]
            if measurement_dir.exists():
                search_dirs.extend(measurement_dir.glob('*/Recordings'))

            latest_ir_mtime = 0
            for directory in search_dirs:
                if directory.exists():
                    wavs = list(directory.glob('*_ir.wav'))
                    if wavs:
                        mtime = max(f.stat().st_mtime for f in wavs)
                        latest_ir_mtime = max(latest_ir_mtime, mtime)

            if latest_ir_mtime > last_ir_mtime:
                last_ir_mtime = latest_ir_mtime
                update_ir_fr_plots()

            await asyncio.sleep(1)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Error watching IR files: {exc}")
            await asyncio.sleep(1)


def build_acoustic_analysis():
    """Build the acoustic analysis panel."""
    global ir_fr_plot

    with ui.column().classes('w-full h-full min-w-0 flex flex-col p-2'):
        ui.label('Acoustic Analysis').classes('text-xl font-bold mb-1')
        ir_fr_plot = ui.matplotlib(figsize=(16, 12)).classes('w-full flex-1')
        ui.button(
            'Refresh Plots',
            icon='refresh',
            on_click=lambda: update_ir_fr_plots(ir_fr_plot),
        ).classes('mt-2')

    ui.timer(0, watch_ir_files, once=True)
    update_ir_fr_plots(ir_fr_plot)
