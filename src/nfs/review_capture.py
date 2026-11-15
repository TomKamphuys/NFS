"""Raw microphone evidence for measurement review; no electrical QA or full-IR scoring."""
from __future__ import annotations

import json
from pathlib import Path
import warnings

import numpy as np
from scipy.io import wavfile
from scipy.spatial import cKDTree

from .capture_detection.loopback_reference import locate_marker

REPORT_COLUMNS = ['filename', 'status', 'category', 'primary_issue', 'score',
                  'spatial_score', 'band_low_hz', 'band_high_hz', 'coverage', 'reason', 'evidence']


def read_audio(path):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', wavfile.WavFileWarning)
        fs, raw = wavfile.read(path)
    data = np.asarray(raw, dtype=float)
    if raw.dtype == np.uint8:
        data = (data - 128) / 128
    elif np.issubdtype(raw.dtype, np.integer):
        data /= float(np.iinfo(raw.dtype).max) + 1
    if not data.size or not np.isfinite(data).all():
        raise ValueError('Empty or non-finite audio')
    return fs, data


def rms(data):
    return float(np.sqrt(np.mean(np.square(data))))


def db(value):
    return float(20 * np.log10(max(value, 1e-15)))


def capture_stem(name):
    name = name.removesuffix('.blacklisted').removesuffix('.wav')
    return name.removesuffix('_ir')


def capture_path(folder, row):
    return Path(folder) / 'debug' / (capture_stem(row['filename']) + '_capture.wav')


def full_ir_path(folder, row):
    name = row['filename'].removesuffix('.blacklisted').removesuffix('.wav')
    return Path(folder) / 'Distortion' / (name + '_dist.wav')


def noise_windows(fs, samples, marker_sample, final_sweep_end):
    """Half-open raw sample ranges. Timing, never measured quietness, selects them."""
    guard = round(.020 * fs)
    minimum = round(.050 * fs)
    pre = (max(guard, round(.2 * marker_sample)), marker_sample - guard)
    post = (final_sweep_end + round(.5 * (samples - final_sweep_end)), samples - guard)
    return {key: pair if 0 <= pair[0] < pair[1] <= samples and pair[1]-pair[0] >= minimum else None
            for key, pair in (('pre', pre), ('tail', post))}


def load_capture_reference(folder, row):
    """Prefer explicit association; legacy sets may use one unambiguous archive."""
    debug = (Path(folder) / 'debug').resolve()
    sidecar = debug / (capture_stem(row['filename']) + '_capture.json')
    timing = {}
    if sidecar.exists():
        timing = json.loads(sidecar.read_text(encoding='utf-8'))
        reference = (debug / timing['reference_file']).resolve()
        if not reference.is_relative_to(debug):
            raise ValueError('Reference path is outside analysis directory')
    else:
        references = sorted((debug / 'references').glob('*/*.npz'))
        if len(references) != 1:
            raise ValueError('No unambiguous capture-to-reference association')
        reference = references[0]
    with np.load(reference, allow_pickle=False) as archive:
        metadata = json.loads(str(archive['metadata_json'].item()))
        if metadata['raw_capture_channels'] != ['electrical_loopback', 'microphone']:
            raise ValueError('Unknown raw capture channel layout')
        layout = metadata['layout']
        if layout['schema'] != 'marker_then_dual_sweep_v1':
            raise ValueError('Unsupported capture sequence')
        marker = archive['marker'].copy()
    return metadata, timing, marker


def microphone_metrics(folder, row):
    fs, raw = read_audio(capture_path(folder, row))
    if raw.ndim != 2 or raw.shape[1] != 2:
        raise ValueError('Expected electrical loopback and microphone channels')
    mic = raw[:, 1]
    issues = []
    if not np.any(mic) or np.ptp(mic) == 0:
        issues.append(('silent_channel', 'repeat', 'Microphone channel is silent or constant.'))
    if np.count_nonzero(abs(mic) >= .999) >= 3:
        issues.append(('clipping', 'repeat', 'Microphone capture reaches digital full scale.'))
    row['issues'].extend(dict(category='Acoustic/microphone', code=code, severity=severity, message=message)
                         for code, severity, message in issues)
    # Basic channel checks remain available even when timing metadata is missing.
    metadata, timing, marker = load_capture_reference(folder, row)
    layout = metadata['layout']
    if fs != metadata['sample_rate'] or len(raw) != layout['total_samples']:
        raise ValueError('Capture length or sample rate differs from reference')
    starts = layout['playback_marker_starts']
    if not starts:
        raise ValueError('Missing sweep timing')
    measured = []
    for index, start in enumerate(starts):
        stop = starts[index+1] - len(marker) if index+1 < len(starts) else len(raw)-len(marker)
        # Same marker detection as acquisition; no residual comparison is performed.
        measured.append(locate_marker(raw[:, 0], marker, max(0, start-round(.1*fs)), stop, .6))
    sweeps = [(start + layout['sweep_offset_samples'],
               start + layout['sweep_offset_samples'] + layout['sweep_samples']) for start in measured]
    if any(not 0 <= start < end <= len(mic) for start, end in sweeps):
        raise ValueError('Aligned sweep timing is outside recording')
    level = np.sqrt(sum(float(mic[a:b] @ mic[a:b]) for a, b in sweeps) /
                    sum(b-a for a, b in sweeps))
    windows = noise_windows(fs, len(mic), measured[0], sweeps[-1][1])
    metrics = row['metrics']
    metrics['mic_sweep_dbfs'] = db(level)
    metrics['sweep_regions_s'] = [[a/fs, b/fs] for a, b in sweeps]
    # Match baseline groups by the acquisition timing and sweep settings, not archive ID.
    row['noise_group'] = json.dumps([fs, layout, metadata.get('sweep')], sort_keys=True)
    for key, window in windows.items():
        if window is None:
            row['coverage_notes'].append(f'Insufficient {"pre-sweep" if key == "pre" else "post-sweep"} silence')
            continue
        a, b = window
        noise = rms(mic[a:b])
        metrics.update({f'mic_{key}_dbfs': db(noise), f'mic_{key}_relative_db': db(noise/max(level, 1e-30)),
                        f'{key}_start_s': a/fs, f'{key}_end_s': b/fs})
    return windows


def compare_noise(rows):
    for key, code, message in (
            ('pre', 'pre_sweep_noise', 'Elevated environmental microphone noise before the marker.'),
            ('tail', 'late_noise', 'Unusual late microphone energy; noise or room decay.')):
        groups = {}
        for row in rows:
            if f'mic_{key}_dbfs' in row['metrics'] and 'xyz' in row:
                groups.setdefault(row['noise_group'], []).append(row)
        for group in groups.values():
            if len(group) < 9:
                for row in group:
                    row['coverage_notes'].append(f'{key} noise baseline unavailable (need 9 matching captures)')
                continue
            values = np.array([row['metrics'][f'mic_{key}_dbfs'] for row in group])
            median = float(np.median(values))
            scale = max(float(1.4826*np.median(abs(values-median))), 1.)
            neighbours = cKDTree([row['xyz'] for row in group]).query(
                [row['xyz'] for row in group], k=min(13, len(group)))[1]
            for i, row in enumerate(group):
                local = values[neighbours[i][neighbours[i] != i]]
                local_median = float(np.median(local))
                local_scale = max(float(1.4826*np.median(abs(local-local_median))), 1.)
                limit = max(median+max(12., 6*scale), local_median+max(12., 6*local_scale))
                row['metrics'][f'mic_{key}_threshold_dbfs'] = limit
                if values[i] > limit and row['metrics'][f'mic_{key}_relative_db'] > -45:
                    row['issues'].append(dict(category='Acoustic/microphone', code=code, severity='review',
                                              message=message, value_db=float(values[i]), threshold_db=limit))


def _capture_evidence(task):
    folder, row = task
    row.update(issues=[], metrics={}, coverage_notes=[], spatial_score=row['score'], spatial_status=row['status'])
    if row['status'] == 'repeat':
        row['issues'].append(dict(category='Acoustic/spatial', code='spatial_discrepancy', severity='repeat',
                                  message='LF response differs from its 12 nearest neighbours; cause unconfirmed.'))
    elif row['status'] == 'technical':
        row['issues'].append(dict(category='File/metadata', code='invalid_ir', severity='technical', message=row['reason']))
    if capture_path(folder, row).exists():
        try:
            microphone_metrics(folder, row)
        except (ValueError, OSError, EOFError, KeyError, TypeError, IndexError) as error:
            row['coverage_notes'].append('Capture noise analysis unavailable: ' + str(error))
    else:
        row['coverage_notes'].append('Raw capture unavailable; microphone checks not assessed')
    return row


def augment_rows(folder, rows, progress, cancelled, *, pool=None):
    active = [row for row in rows if row['status'] != 'excluded']
    tasks = ((folder, row) for row in active)
    results = (pool.map(_capture_evidence, tasks, cancelled) if pool is not None
               else map(_capture_evidence, tasks))
    for i, result in enumerate(results):
        if cancelled():
            raise InterruptedError('Scan cancelled.')
        active[i].update(result)
        progress('Checking microphone evidence', i+1, len(active))
    compare_noise(active)
    for row in active:
        row['coverage'] = '; '.join(row['coverage_notes']) or 'Linear IR + microphone capture + noise baselines'
        issues = row['issues']
        row['primary_issue'] = issues[0]['code'] if issues else ''
        row['category'] = '; '.join(dict.fromkeys(issue['category'] for issue in issues))
        if issues:
            row['status'] = max((issue['severity'] for issue in issues), key={'technical': 3, 'repeat': 2, 'review': 1}.get)
            row['reason'] = ' '.join(dict.fromkeys(issue['message'] for issue in issues))
        if row['status'] == 'pass' and row['coverage_notes']:
            row['status'] = 'incomplete'
        row['evidence'] = json.dumps(dict(metrics=row['metrics'], issues=issues, bands=row.get('bands', []), neighbours=row.get('neighbours', [])), allow_nan=False)
