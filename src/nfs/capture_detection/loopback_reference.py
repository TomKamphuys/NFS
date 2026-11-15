"""Experimental loopback reference detector, adapted from loopback_reference_poc.py.

The golden waveform is the sample-wise median of three single-sweep captures.
Method v5 uses a -40 dB active-sweep RMS floor with a smooth transition into
the existing HF allowance,
including 6 dB additional margin, for every raw repeat before DSP averaging.
Older archives remain replayable with their saved limits and alignment layout.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from collections.abc import Mapping
import json

import numpy as np
from scipy.fft import irfft, next_fast_len, rfft, rfftfreq
from scipy.optimize import minimize_scalar
from scipy.signal import correlate
from loguru import logger

from .base import CaptureContext, CaptureIssue, DetectorSession
from .events import DetectorEvent

VERSION = 5
ERROR_FLAGS = ('input_underflow', 'input_overflow',
               'output_underflow', 'output_overflow')


@dataclass
class Layout:
    fs: int
    total_samples: int
    marker_starts: list[int]
    marker_samples: int
    sweep_offset: int
    sweep_samples: int
    tail_samples: int
    guard_samples: int
    log_sweep_rate: float
    start_hz: float = 1.0
    comparison_tail_samples: int | None = None

    @property
    def repeat_samples(self) -> int:
        # Leave room for fractional interpolation before the following marker.
        tail = self.tail_samples if self.comparison_tail_samples is None else self.comparison_tail_samples
        return self.sweep_offset + self.sweep_samples + tail - 16

    def with_repeats(self, count: int) -> Layout:
        """Describe the same playback format with a different known repeat count."""
        if count < 1:
            raise ValueError('Repeat count must be positive')
        slot = self.sweep_offset + self.sweep_samples + self.tail_samples
        return replace(self, total_samples=self.total_samples + (count - len(self.marker_starts)) * slot,
                       marker_starts=[self.marker_starts[0] + i * slot for i in range(count)])


@dataclass
class Limits:
    # Retained for replay of v1/v2 references using the original local-RMS rule.
    relative_rms: float = .03
    overall_rms_floor: float = .005
    window_ms: float = 10.
    step_ms: float = 5.
    minimum_marker_correlation: float = .6
    threshold_method: str = 'frequency_rms_v2'
    rms_normalization: str = 'active_sweep'
    frequency_rms_floor: float = .0027
    frequency_rms_per_hz: float = 6.3e-7
    frequency_margin_db: float = 6.
    minimum_rms_ratio: float = .01

    def __post_init__(self):
        if self.threshold_method not in ('local_rms_v1', 'frequency_rms_v1', 'frequency_rms_v2'):
            raise ValueError('Unsupported golden loopback threshold method')
        if self.rms_normalization not in ('whole_reference', 'active_sweep'):
            raise ValueError('Unsupported golden loopback RMS normalization')
        for name in ('relative_rms', 'overall_rms_floor', 'frequency_rms_floor',
                     'frequency_rms_per_hz', 'frequency_margin_db', 'window_ms', 'step_ms', 'minimum_rms_ratio'):
            if not np.isfinite(getattr(self, name)):
                raise ValueError(f'Non-finite golden loopback limit: {name}')
        if (min(self.relative_rms, self.overall_rms_floor, self.frequency_rms_per_hz, self.minimum_rms_ratio) < 0
                or min(self.frequency_rms_floor, self.window_ms, self.step_ms) <= 0):
            raise ValueError('Invalid golden loopback residual limits')


@dataclass
class Capture:
    loopback: np.ndarray
    driver_events: list[dict] = field(default_factory=list)


@dataclass
class Reference:
    waveform: np.ndarray
    marker_anchor: np.ndarray
    layout: Layout
    limits: Limits
    method_version: int = VERSION


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def validate_capture(capture: Capture, layout: Layout) -> None:
    if capture.loopback.shape != (layout.total_samples,):
        raise ValueError('Incomplete recording or incompatible capture length')
    if not np.isfinite(capture.loopback).all():
        raise ValueError('Non-finite loopback samples')
    if any(event.get(flag, False) for event in capture.driver_events for flag in ERROR_FLAGS):
        raise ValueError('Driver underflow/overflow')


def locate_marker(x: np.ndarray, marker: np.ndarray, start: int,
                  stop: int, minimum: float) -> int:
    start = max(0, start)
    stop = min(stop, len(x) - len(marker))
    if stop < start:
        raise ValueError('Marker search outside recording')
    part = x[start:stop + len(marker)]
    correlation = correlate(part, marker, mode='valid', method='fft')
    energy = np.r_[0., np.cumsum(part * part, dtype=np.float64)]
    energy = np.maximum(energy[len(marker):] - energy[:-len(marker)], 0.)
    denominator = np.sqrt(energy * np.sum(marker * marker))
    scores = np.divide(correlation, denominator, out=np.zeros_like(correlation),
                       where=denominator > 1e-15)
    index = int(np.argmax(scores))
    if scores[index] < minimum or rms(part[index:index + len(marker)]) < 1e-10:
        raise ValueError(f'Unreliable marker (correlation {scores[index]:.4f})')
    return start + index


def shifted_segment(x: np.ndarray, start: float, length: int) -> np.ndarray:
    """Sample x[start:start+length] using zero-padded Fourier interpolation."""
    base = int(np.floor(start))
    if base < 0 or start + length + 2 > len(x):
        raise ValueError('Aligned recording is truncated')
    margin = 128
    left = max(0, base - margin)
    right = min(len(x), base + length + margin)
    part = x[left:right]
    size = next_fast_len(2 * len(part))
    phase = np.exp(2j * np.pi * rfftfreq(size) * (start - base))
    shifted = irfft(rfft(part, size) * phase, size)
    offset = base - left
    return shifted[offset:offset + length]


def refine_marker(x: np.ndarray, integer: int, anchor: np.ndarray) -> float:
    # Filter phase can offset the digital peak. Only the measured marker, never
    # the sweep body, determines the final fractional transport delay.
    def objective(position: float) -> float:
        measured = shifted_segment(x, position, len(anchor))
        return -float(measured @ anchor) / max(rms(measured) * rms(anchor), 1e-20)

    fit = minimize_scalar(objective, bounds=(integer - 1.5, integer + 1.5),
                          method='bounded', options={'xatol': 1e-5})
    if not fit.success:
        raise ValueError('Fractional marker alignment failed')
    return float(fit.x)


def align_capture(capture: Capture, layout: Layout, marker: np.ndarray,
                  limits: Limits, anchor: np.ndarray | None = None
                  ) -> tuple[np.ndarray, list[float], np.ndarray]:
    validate_capture(capture, layout)
    rows, positions = [], []
    first_delay = 0.
    for repeat, planned in enumerate(layout.marker_starts):
        if repeat == 0:
            low, high = planned - round(.1 * layout.fs), planned + round(.3 * layout.fs)
        else:
            expected = planned + round(first_delay)
            low, high = expected - round(.005 * layout.fs), expected + round(.005 * layout.fs)
        integer = locate_marker(capture.loopback, marker, low, high,
                                limits.minimum_marker_correlation)
        if anchor is None:
            anchor = capture.loopback[integer:integer + layout.marker_samples].copy()
        position = refine_marker(capture.loopback, integer, anchor)
        if repeat == 0:
            first_delay = position - planned
        rows.append(shifted_segment(capture.loopback, position, layout.repeat_samples))
        positions.append(position)
    return np.asarray(rows), positions, anchor


def residual_windows(recorded: np.ndarray, reference: np.ndarray,
                     layout: Layout, limits: Limits) -> dict:
    """Null-test measurements, independent of the acceptance thresholds."""
    window = max(1, round(layout.fs * limits.window_ms / 1000))
    step = max(1, round(layout.fs * limits.step_ms / 1000))
    count = len(reference)
    if recorded.shape != reference.shape or recorded.ndim != 1 or count < window:
        raise ValueError('Recording shorter than comparison window')
    starts = np.unique(np.r_[np.arange(0, count - window + 1, step), count - window])
    residual_energy = np.r_[0., np.cumsum((recorded - reference) ** 2)]
    reference_energy = np.r_[0., np.cumsum(reference ** 2)]
    active_start = layout.sweep_offset + layout.guard_samples
    active_end = layout.sweep_offset + layout.sweep_samples - layout.guard_samples
    if not 0 <= active_start < active_end <= count:
        raise ValueError('Invalid golden loopback active sweep bounds')
    values = {
        'starts': starts,
        'residual_rms': np.sqrt(np.maximum(residual_energy[starts + window] - residual_energy[starts], 0.) / window),
        'local_reference_rms': np.sqrt(np.maximum(reference_energy[starts + window] - reference_energy[starts], 0.) / window),
        'reference_rms': rms(reference),
        'sweep_reference_rms': rms(reference[active_start:active_end]),
    }
    return values


def compare_waveforms(actual: np.ndarray, expected: np.ndarray,
                      layout: Layout, limits: Limits) -> dict:
    if actual.ndim != 2 or expected.ndim != 2 or actual.shape[1] != expected.shape[1]:
        raise ValueError('Incompatible waveform sections')
    if expected.shape[0] == 1:
        expected = np.broadcast_to(expected, actual.shape)
    worst = dict(score=0., repeat=0, window_start_s=0., residual_rms=0., limit_rms=0.)
    failures = 0
    for repeat, (recorded, reference) in enumerate(zip(actual, expected, strict=True)):
        values = residual_windows(recorded, reference, layout, limits)
        starts, residual = values['starts'], values['residual_rms']
        threshold = residual_thresholds(values, layout, limits)
        scores = residual / threshold
        index = int(np.argmax(scores))
        failures += int(np.count_nonzero(scores > 1.))
        if scores[index] > worst['score']:
            worst = dict(score=float(scores[index]), repeat=repeat + 1,
                         window_start_s=float(starts[index] / layout.fs),
                         residual_rms=float(residual[index]), limit_rms=float(threshold[index]))
    return dict(accepted=failures == 0, reason='matched' if failures == 0 else 'waveform mismatch',
                failed_windows=failures, **worst)


def frequency_residual_ratio(frequency: np.ndarray, limits: Limits) -> np.ndarray:
    """Saved frequency curve, optionally joined smoothly to the new RMS floor.

    Compact quadratic smooth maximum in dB: never below either curve, with
    matching first derivatives at both joins. The 4 dB half-width lets the
    transition extend slightly beyond 10 kHz, rejoining the old curve at 11.9 kHz.
    """
    ratio = (10 ** (limits.frequency_margin_db / 20)
             * np.hypot(limits.frequency_rms_floor, limits.frequency_rms_per_hz * frequency))
    if limits.threshold_method == 'frequency_rms_v2' and limits.minimum_rms_ratio > 0:
        floor_db = 20*np.log10(limits.minimum_rms_ratio)
        curve_db = 20*np.log10(ratio)
        width_db = 4.
        excess_db = np.maximum(width_db-np.abs(curve_db-floor_db), 0.)**2/(4*width_db)
        # Preserve exact values outside the transition, including the flat floor.
        ratio = np.maximum(ratio, limits.minimum_rms_ratio)*10**(excess_db/20)
    return ratio


def residual_thresholds(values: dict, layout: Layout, limits: Limits) -> np.ndarray:
    """Absolute RMS allowance for the same windows returned by residual_windows.

    The new rule never divides by short-window signal RMS. Marker/gap/tail use
    a constant floor. Windows overlapping the sweep (including its guards) use
    centre frequency clamped to active-sweep endpoints, covering filtered sweep
    ends without assigning the HF allowance to the remainder of the quiet tail.
    """
    if limits.threshold_method == 'local_rms_v1':
        return np.maximum(values['local_reference_rms'] * limits.relative_rms,
                          max(values['reference_rms'] * limits.overall_rms_floor, 1e-12))
    window = max(1, round(layout.fs * limits.window_ms / 1000))
    starts = values['starts']
    sweep_end = layout.sweep_offset + layout.sweep_samples
    overlaps = (starts + window > layout.sweep_offset) & (starts < sweep_end)
    active_start = layout.sweep_offset + layout.guard_samples
    active_end = sweep_end - layout.guard_samples
    if (layout.fs <= 0 or layout.log_sweep_rate <= 0 or layout.start_hz <= 0
            or active_end <= active_start):
        raise ValueError('Invalid golden loopback sweep timing')
    elapsed = np.clip(starts + window / 2 - active_start, 0, active_end - active_start) / layout.fs
    frequency = np.where(overlaps, layout.start_hz * np.exp(elapsed / layout.log_sweep_rate), 0.)
    ratio = frequency_residual_ratio(frequency, limits)
    normalizer = (values['sweep_reference_rms'] if limits.rms_normalization == 'active_sweep'
                  else values['reference_rms'])
    return np.maximum(normalizer * ratio, 1e-12)


def establish_reference(captures: list[Capture], layout: Layout,
                        marker: np.ndarray, limits: Limits) -> tuple[Reference, list[dict]]:
    if len(captures) != 3:
        raise ValueError('Exactly three independent captures are required')
    if len(layout.marker_starts) != 1:
        raise ValueError('Each reference capture must contain exactly one sweep')
    rows, anchor = [], None
    for capture in captures:
        aligned, _, anchor = align_capture(capture, layout, marker, limits, anchor)
        rows.append(aligned)
    pairs = []
    for left, right in ((0, 1), (0, 2), (1, 2)):
        # Both directions must pass with each capture's own reference RMS.
        forward = compare_waveforms(rows[left], rows[right], layout, limits)
        backward = compare_waveforms(rows[right], rows[left], layout, limits)
        result = max((forward, backward), key=lambda item: item['score'])
        pairs.append(dict(pair=[left + 1, right + 1], **result))
    if not all(result['accepted'] for result in pairs):
        raise ValueError('Initial loopbacks disagree: ' + json.dumps(pairs))
    reference = Reference(np.median(rows, axis=0), anchor, layout, limits)
    # Validate against the exact median too, not only against the other captures.
    for row in rows:
        if not compare_waveforms(row, reference.waveform, layout, limits)['accepted']:
            raise ValueError('Initial capture disagrees with median reference')
    return reference, pairs


def check_capture(capture: Capture, reference: Reference, marker: np.ndarray,
                  layout: Layout | None = None) -> dict:
    try:
        layout = layout or reference.layout
        if reference.method_version == 1:
            compatible = layout == reference.layout
        else:
            compatible = all(getattr(layout, key) == getattr(reference.layout, key) for key in (
                'fs', 'marker_samples', 'sweep_offset', 'sweep_samples', 'tail_samples', 'comparison_tail_samples',
                'guard_samples', 'log_sweep_rate', 'start_hz'))
        if not compatible:
            raise ValueError('Capture settings differ from golden loopback reference')
        aligned, positions, _ = align_capture(capture, layout, marker,
                                             reference.limits, reference.marker_anchor)
        return dict(marker_positions=positions, **compare_waveforms(
            aligned, reference.waveform, layout, reference.limits))
    except ValueError as error:
        return dict(accepted=False, reason=str(error))


@dataclass
class GoldenLoopback:
    reference: Reference
    marker: np.ndarray
    bootstrap_pairs: list[dict]


def archive_loopback_reference(golden: GoldenLoopback) -> dict[str, np.ndarray]:
    """Lossless, pickle-free fields added to the existing shared reference NPZ."""
    metadata = {
        'schema': f'loopback_reference_v{golden.reference.method_version}',
        'method_version': golden.reference.method_version,
        'aggregation': 'median', 'bootstrap_capture_count': 3,
        'reference_sweep_count': len(golden.reference.layout.marker_starts),
        'layout': asdict(golden.reference.layout),
        'limits': asdict(golden.reference.limits),
        'bootstrap_pairs': golden.bootstrap_pairs,
    }
    return {
        'loopback_reference_waveform': golden.reference.waveform,
        'loopback_reference_marker_anchor': golden.reference.marker_anchor,
        'loopback_reference_marker': golden.marker,
        'loopback_reference_metadata_json': np.array(json.dumps(
            metadata, sort_keys=True, separators=(',', ':'), allow_nan=False)),
    }


def load_loopback_reference(archive: Mapping[str, np.ndarray]) -> GoldenLoopback:
    """Restore a golden reference from np.load(..., allow_pickle=False).

    Copies own their memory, so the returned reference remains usable after the
    NPZ is closed. Archives predating this detector have no such fields.
    """
    metadata = json.loads(archive['loopback_reference_metadata_json'].item())
    version = metadata['method_version']
    if (version not in (1, 2, 3, 4, VERSION) or metadata['schema'] != f'loopback_reference_v{version}'
            or metadata['aggregation'] != 'median'):
        raise ValueError('Unsupported golden loopback reference format')
    layout = Layout(**metadata['layout'])
    saved_limits = dict(metadata['limits'])
    if version < 3:
        saved_limits.setdefault('threshold_method', 'local_rms_v1')
    elif 'threshold_method' not in saved_limits:
        raise ValueError('Missing golden loopback threshold method')
    if version < 4:
        saved_limits.setdefault('rms_normalization', 'whole_reference')
    elif 'rms_normalization' not in saved_limits:
        raise ValueError('Missing golden loopback RMS normalization')
    limits = Limits(**saved_limits)
    waveform = np.array(archive['loopback_reference_waveform'], dtype=np.float64, copy=True)
    anchor = np.array(archive['loopback_reference_marker_anchor'], dtype=np.float64, copy=True)
    marker = np.array(archive['loopback_reference_marker'], dtype=np.float64, copy=True)
    if (waveform.shape != (len(layout.marker_starts), layout.repeat_samples)
            or anchor.shape != (layout.marker_samples,) or marker.shape != anchor.shape
            or not all(np.isfinite(values).all() for values in (waveform, anchor, marker))):
        raise ValueError('Invalid golden loopback reference arrays')
    if version >= 2 and len(layout.marker_starts) != 1:
        raise ValueError('Single-sweep reference must contain exactly one section')
    return GoldenLoopback(Reference(waveform, anchor, layout, limits, version), marker,
                          metadata['bootstrap_pairs'])


def capture_input(result: dict, fs: int) -> tuple[Capture, Layout, np.ndarray]:
    """Adapt a completed acquisition; use raw repeats, never their DSP average."""
    timing = result['capture_metadata']
    marker = np.asarray(result['digital_reference']['marker'], dtype=np.float64)
    layout = Layout(
        fs, timing['total_samples'], list(timing['playback_marker_starts']),
        timing['marker_samples'], timing['sweep_offset_samples'], timing['sweep_samples'],
        timing.get('requested_tail_samples', timing['capture_tail_samples']), timing['guard_samples'], timing['L_s'],
        timing['sweep_start_hz'],
        # New recordings have no extra latency flush. Compare through the sweep;
        # room-decay tail availability can vary with transport latency.
        comparison_tail_samples=0 if timing.get('flush_samples') == 0 else None)
    if (not layout.marker_starts or marker.shape != (layout.marker_samples,)
            or not np.isfinite(marker).all() or rms(marker) < 1e-10):
        raise ValueError('Invalid reference marker or repeat layout')
    events = []
    for event in timing.get('stream_status', []):
        flags = {key: bool(event.get(key, False)) for key in ERROR_FLAGS}
        # Older recordings and synthetic backends may supply only status text.
        text = str(event.get('status', '')).lower()
        if 'underflow' in text or 'overflow' in text:
            flags['input_underflow'] = True
        events.append(flags)
    return (Capture(np.asarray(result['raw_recordings'][0], dtype=np.float64), events),
            layout, marker)


def prepare_loopback_reference(session: DetectorSession) -> GoldenLoopback:
    """Build one template from three single-sweep captures, regardless of averaging."""
    logger.info('Preparing golden loopback reference: capturing three single sweeps')
    session.report(DetectorEvent('preparing', 'Loopback reference',
        'Capturing loopbacks to use as a reference for capture fault detection.',
        detector='loopback_reference', total=3, duration_s=session.single_capture_duration_s))
    captures = []
    layout = marker = None
    for index in range(3):
        result = session.acquire(num_sweeps=1)
        capture, observed_layout, observed_marker = capture_input(result, session.fs)
        if layout is None:
            layout, marker = observed_layout, observed_marker.copy()
        elif observed_layout != layout or not np.array_equal(observed_marker, marker):
            raise ValueError('Golden loopback reference failed: capture settings changed during preparation')
        captures.append(capture)
        logger.info(f'Golden loopback reference: acquired sequence {index + 1}/3')
        session.report(DetectorEvent('preparation_progress', 'Loopback reference',
            f'Captured reference sequence {index + 1} of 3.', detector='loopback_reference',
            completed=index + 1, total=3, duration_s=session.single_capture_duration_s))
    session.report(DetectorEvent('checking', 'Loopback reference',
        'Checking that all three reference loopbacks match…', detector='loopback_reference',
        completed=3, total=3))
    try:
        reference, pairs = establish_reference(captures, layout, marker, Limits())
    except ValueError as error:
        raise ValueError(f'Golden loopback reference failed: {error}') from error
    logger.info('Golden loopback reference ready; worst bootstrap score '
                f"{max(pair['score'] for pair in pairs):.3f} (limit 1.0)")
    session.report(DetectorEvent('preparation_ready', 'Loopback reference',
        'The three reference loopbacks match.', detector='loopback_reference', completed=3, total=3,
        hold_seconds=1.0))
    return GoldenLoopback(reference, marker, pairs)


def loopback_reference(context: CaptureContext) -> CaptureIssue | None:
    """Check against this set's fixed reference; standalone/preview captures skip it."""
    if not context.measurement_set:
        return None
    golden = context.detector_state.get('loopback_reference')
    if golden is None:
        return CaptureIssue('loopback_reference', 'Golden loopback reference was not prepared')
    try:
        capture, layout, marker = capture_input(context.capture, context.fs)
        if not np.array_equal(marker, golden.marker):
            raise ValueError('Capture settings differ from golden loopback reference')
        result = check_capture(capture, golden.reference, golden.marker, layout)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        result = {'accepted': False, 'reason': str(error)}
    if not result['accepted']:
        reason = f"Golden loopback mismatch: {result['reason']}"
        if 'score' in result:
            reason += (f" (score={result['score']:.3f}, repeat={result['repeat']}, "
                       f"position={result['window_start_s']:.4f}s)")
        logger.warning(reason)
        return CaptureIssue('loopback_reference', reason, result)
    return None
