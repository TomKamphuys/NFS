"""Matplotlib inspection plots embedded in the native review pane."""
import numpy as np
import textwrap
from matplotlib.ticker import NullFormatter
from nfs.measurement_review import FREQUENCIES, robust_predict
from nfs.review_capture import read_audio, capture_path
from .review_waveform import AdaptiveWaveform

def draw_capture_evidence(microphone, folder, row):
    microphone.set(title='Microphone capture', xlabel='Time (s)', ylabel='Amplitude')
    try:
        fs, raw = read_audio(capture_path(folder, row))
        if raw.ndim != 2 or raw.shape[1] != 2:
            raise ValueError('Expected a two-channel capture')
        plot_envelope(microphone, raw[:, 1], fs)
        metrics = row.get('metrics', {})
        sweeps = metrics.get('sweep_regions_s', [])
        if not sweeps:
            # Older saved review results predate the explicit sweep-region metric.
            from nfs.review_capture import microphone_metrics
            temporary = dict(row, metrics={}, issues=[], coverage_notes=[])
            try:
                microphone_metrics(folder, temporary)
                sweeps = temporary['metrics'].get('sweep_regions_s', [])
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                pass
        for index, (start, end) in enumerate(sweeps):
            microphone.axvspan(start, end, color='#a78bfa', alpha=.2,
                               label='Sweep' if index == 0 else None)
        for key, color, label in (('pre', 'tab:green', 'Pre-sweep analysis'),
                                  ('tail', 'tab:orange', 'Post-sweep analysis')):
            if f'{key}_start_s' in metrics:
                microphone.axvspan(metrics[f'{key}_start_s'], metrics[f'{key}_end_s'],
                                   color=color, alpha=.2, label=label)
        if microphone.get_legend_handles_labels()[0]:
            microphone.legend(fontsize=8, loc='upper left')
    except (OSError, ValueError, EOFError) as error:
        microphone.text(.02, .9, textwrap.fill(str(error), 60), transform=microphone.transAxes,
                        fontsize=8, va='top', clip_on=True)


def draw_inspection(figure, folder, row, rows):
    """Render full capture evidence, LF predictions and measurement locations."""
    figure.clear()
    grid = figure.add_gridspec(3, 1)
    microphone = figure.add_subplot(grid[0, 0])
    spectrum = figure.add_subplot(grid[1, 0])
    spatial = figure.add_subplot(grid[2, 0])
    axes = (microphone, spectrum, spatial)
    draw_capture_evidence(microphone, folder, row)
    spectrum.set(title='LF response and local predictions', xlabel='Frequency (Hz)', ylabel='Magnitude (dB)')
    spatial.set(title='Measurement locations', xlabel='Azimuth (degrees)', ylabel='Height (mm)')
    lookup = {r['filename']: r for r in rows}
    neighbours = [lookup[name] for name in row.get('neighbours', []) if name in lookup]
    if 'response' in row:
        level = lambda response: 20*np.log10(np.maximum(abs(response), 1e-30))
        for neighbour in neighbours:
            spectrum.semilogx(FREQUENCIES, level(neighbour['response']), color='0.75', alpha=.5, lw=.6)
        spectrum.semilogx(FREQUENCIES, level(row['response']), color='tab:red', label='Selected', lw=1.6)
        if neighbours:
            local = [row]+neighbours
            xyz = np.array([r['xyz'] for r in local])
            response = np.array([r['response'] for r in local])
            for count, color in ((12, 'tab:blue'),):
                # Fit separately in each octave, exactly as the detector does.
                prediction = np.empty(len(FREQUENCIES), complex)
                for low, high in ((40, 80), (80, 160), (160, 321)):
                    band = (FREQUENCIES >= low-1e-8) & (FREQUENCIES < high-1e-8)
                    prediction[band] = robust_predict(xyz, response[:, band], 0, np.arange(1, count+1))[0]
                spectrum.semilogx(FREQUENCIES, level(prediction), color=color, ls='--', label=f'{count}-neighbour prediction')
        spectrum.legend(fontsize=8, loc='lower left')
        spectrum.set_xticks([40, 60, 80, 120, 160, 240, 320], labels=['40','60','80','120','160','240','320'])
        spectrum.xaxis.set_minor_formatter(NullFormatter())
    located = [r for r in rows if 'cylindrical' in r]
    if located:
        spatial.scatter([r['cylindrical'][1] for r in located], [r['cylindrical'][2] for r in located], s=7, c='0.8')
    if neighbours:
        spatial.scatter([r['cylindrical'][1] for r in neighbours], [r['cylindrical'][2] for r in neighbours], s=16, c='tab:blue', label='Neighbours')
    if 'cylindrical' in row:
        spatial.scatter([row['cylindrical'][1]], [row['cylindrical'][2]], s=65, c='tab:red', marker='x', label='Selected')
        spatial.legend(fontsize=8, loc='lower left')
    for ax in axes:
        ax.grid(alpha=.2)
    return axes


def plot_envelope(ax, signal, fs):
    waveform = AdaptiveWaveform(ax, signal, fs)
    # Matplotlib callbacks hold weak references; keep the controller with its axes.
    ax._review_waveform = waveform
    return waveform

