"""Capture assessment only: no hardware, DSP processing, retries or file writes."""

import numpy as np
from loguru import logger

from .base import CaptureContext, CaptureIssue

def microphone_gaps(context: CaptureContext) -> CaptureIssue | None:
    """Scan the raw microphone recording for sudden energy drops (dropouts).

    Unlike the loopback channel, the mic can have legitimately low signal
    (e.g., behind the speaker). Instead of an absolute threshold, this method
    compares each small window's energy against a local envelope computed from
    wider neighboring windows. A sudden drop of more than 20 dB below the
    local envelope is flagged as a silence gap.

    Returns an issue if a silence gap is found (needs retry), None if clean.
    """
    result = context.capture
    raw_recordings = result.get('raw_recordings')
    if raw_recordings is None or len(raw_recordings) < 2:
        return None

    rec_mic = raw_recordings[1]  # microphone channel
    fs = context.fs

    # Use the detected alignment positions to find the actual sweep region.
    aligned_repeats = result['capture_metadata'].get('aligned_repeats', [])
    if not aligned_repeats:
        return None  # No alignment data — skip this check

    first = aligned_repeats[0]
    sweep_start = first.get('sweep_start_sample', 0)
    sweep_samples = result['capture_metadata'].get('sweep_samples', 0)
    guard_samples = result['capture_metadata'].get('guard_samples', 0)
    sweep_end = sweep_start + sweep_samples
    if sweep_end > len(rec_mic):
        sweep_end = len(rec_mic)
    if sweep_end <= sweep_start:
        return None

    # Skip guard samples at the start and end of the sweep signal.
    check_start = sweep_start + guard_samples
    check_end = sweep_end - guard_samples
    if check_end <= check_start:
        return None  # Sweep is too short to check

    sweep_region = rec_mic[check_start:check_end]
    if len(sweep_region) == 0:
        return None

    # Check for overall silence — if the entire mic region is silent,
    # something else is wrong (not a local dropout).
    peak_energy = np.max(np.abs(sweep_region))
    if peak_energy < 1e-12:
        rms = float(np.sqrt(np.mean(np.square(sweep_region, dtype=np.float64))))
        logger.warning(
            f"Microphone sweep is effectively silent: sample_range=[{check_start}, {check_end}), "
            f"peak={peak_energy:.6g}, rms={rms:.6g}; mic silence-gap check skipped")
        return None

    # Compute RMS for every 10 ms window with 50% overlap. Then compare each
    # window's RMS against the median of its neighboring windows (excluding
    # itself). A sudden dropout will have a much lower RMS than its neighbors,
    # while a gradual energy rise (exponential sweep) will show the same trend
    # in all neighboring windows, so no drop is detected.
    check_window = max(1, int(0.010 * fs))  # 10 ms
    check_step = check_window // 2  # 50% overlap

    # Pre-compute RMS for all windows
    num_windows = (len(sweep_region) - check_window) // check_step + 1
    rms_values = np.zeros(num_windows, dtype=np.float64)
    for idx in range(num_windows):
        i = idx * check_step
        window = sweep_region[i:i + check_window]
        rms_values[idx] = np.sqrt(np.mean(window ** 2, dtype=np.float64))

    # Number of neighboring windows to include on each side for the median
    # comparison. A dropout should be obvious within a few adjacent windows.
    neighbor_radius = 5  # 5 ms window spacing; five left and four right windows

    for idx in range(num_windows):
        rms = rms_values[idx]

        # Collect neighboring RMS values (exclude the window itself)
        left_start = max(0, idx - neighbor_radius)
        left_end = idx
        right_start = idx + 1
        right_end = min(num_windows, idx + neighbor_radius)

        neighbors = []
        if left_start < left_end:
            neighbors.append(rms_values[left_start:left_end])
        if right_start < right_end:
            neighbors.append(rms_values[right_start:right_end])

        if not neighbors:
            continue  # No neighbors — skip

        neighbor_arr = np.concatenate(neighbors)
        median_neighbor = float(np.median(neighbor_arr))

        if median_neighbor < 1e-15:
            continue  # No signal in neighbors — skip

        # A zero-RMS window with non-zero neighbors is an infinite drop — always a gap
        if rms < 1e-15:
            i = idx * check_step
            position_ms = (check_start + i) / fs * 1000.0
            logger.warning(
                f"Microphone silence gap: position={position_ms:.1f}ms, "
                f"rms={rms:.6g}, neighbor_median={median_neighbor:.6g}, "
                f"drop=infinite, sweep_check_range=[{check_start}, {check_end})")
            return CaptureIssue('microphone_gaps', 'silence gap in microphone')  # Silence gap detected

        # Check if RMS drops more than 20 dB below the median of neighbors
        drop_db = 20.0 * np.log10(rms / median_neighbor)
        if drop_db < -20.0:
            i = idx * check_step
            position_ms = (check_start + i) / fs * 1000.0
            logger.warning(
                f"Microphone silence gap: position={position_ms:.1f}ms, "
                f"rms={rms:.6g}, neighbor_median={median_neighbor:.6g}, "
                f"drop={drop_db:.1f}dB, sweep_check_range=[{check_start}, {check_end})")
            return CaptureIssue('microphone_gaps', 'silence gap in microphone')  # Silence gap detected

    return None

