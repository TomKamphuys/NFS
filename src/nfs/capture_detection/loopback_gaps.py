"""Capture assessment only: no hardware, DSP processing, retries or file writes."""

import numpy as np
from loguru import logger

from .base import CaptureContext, CaptureIssue

def loopback_gaps(context: CaptureContext) -> CaptureIssue | None:
    """Scan the raw loopback recording for silence gaps indicating dropout.

    The loopback channel always has strong signal — a silence gap is always
    a dropout, never a legitimate low-signal condition.

    Uses the detected alignment positions from the first repeat to find
    the sweep region, so hardware latency is handled correctly.

    Skips the sweep's guard samples (silence at start/end of the sweep
    signal) to avoid false positives.

    Returns an issue if a silence gap is found (needs retry), None if clean.
    """
    result = context.capture
    raw_recordings = result.get('raw_recordings')
    if raw_recordings is None or len(raw_recordings) < 1:
        return None

    rec_loop = raw_recordings[0]  # electrical loopback channel
    fs = context.fs

    # Use the detected alignment positions to find the actual sweep region.
    # This handles hardware latency correctly — the signal may be shifted
    # relative to the planned positions.
    aligned_repeats = result['capture_metadata'].get('aligned_repeats', [])
    if not aligned_repeats:
        return None  # No alignment data — skip this check

    first = aligned_repeats[0]
    sweep_start = first.get('sweep_start_sample', 0)
    sweep_samples = result['capture_metadata'].get('sweep_samples', 0)
    guard_samples = result['capture_metadata'].get('guard_samples', 0)
    sweep_end = sweep_start + sweep_samples
    if sweep_end > len(rec_loop):
        sweep_end = len(rec_loop)
    if sweep_end <= sweep_start:
        return None

    # Skip guard samples at the start and end of the sweep signal.
    # The sweep signal has silent guards to keep endpoint artifacts away.
    check_start = sweep_start + guard_samples
    check_end = sweep_end - guard_samples
    if check_end <= check_start:
        return None  # Sweep is too short to check

    # Sliding window RMS energy detection (10 ms windows)
    window_size = max(1, int(0.010 * fs))  # 10 ms window
    step_size = window_size // 2  # 50% overlap

    sweep_region = rec_loop[check_start:check_end]
    if len(sweep_region) == 0:
        return None

    peak_energy = np.max(np.abs(sweep_region))
    if peak_energy < 1e-12:
        rms = float(np.sqrt(np.mean(np.square(sweep_region, dtype=np.float64))))
        logger.warning(
            f"Loopback sweep is effectively silent: sample_range=[{check_start}, {check_end}), "
            f"peak={peak_energy:.6g}, rms={rms:.6g}; silence-gap check cannot identify a window")
        return None  # Entire sweep is silence — something else is wrong

    # Threshold: -60 dB relative to peak
    threshold = peak_energy * (10 ** (-60.0 / 20.0))

    # Slide across the sweep region checking for silence gaps
    for i in range(0, len(sweep_region) - window_size + 1, step_size):
        window = sweep_region[i:i + window_size]
        rms = np.sqrt(np.mean(window ** 2))
        if rms < threshold:
            logger.warning(
                f"Loopback silence gap: window_start_sample={check_start + i}, "
                f"rms={rms:.6g}, threshold={threshold:.6g}, peak={peak_energy:.6g}, "
                f"sweep_check_range=[{check_start}, {check_end}), window_samples={window_size}")
            return CaptureIssue('loopback_gaps', 'silence gap in loopback')  # Silence gap detected

    return None

