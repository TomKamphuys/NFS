"""Locate the active sweep for raw gap checks, without DSP averaging."""

from .base import CaptureContext
from .loopback_reference import locate_marker


def first_sweep_start(context: CaptureContext) -> int | None:
    # Archived/pre-aligned inputs may already carry a measured position.
    timing = context.capture['capture_metadata']
    repeats = timing.get('aligned_repeats', [])
    if repeats:
        return repeats[0].get('sweep_start_sample', 0)
    if 'first_sweep_start' not in context.analysis_cache:
        result = None
        marker = context.capture.get('digital_reference', {}).get('marker')
        if marker is not None:
            try:
                # Match the first DSP slot's search range, but inspect only the
                # loopback marker. Never shift samples or average any repeats.
                index = locate_marker(
                    context.capture['raw_recordings'][0], marker, 0,
                    timing['pre_samples'] + timing['slot_samples'] - len(marker), .6)
                result = index + timing['sweep_offset_samples']
            except ValueError:
                # Unusable markers are handled by the reference detector or by
                # the subsequent DSP alignment gate if that detector is off.
                pass
        context.analysis_cache['first_sweep_start'] = result
    return context.analysis_cache['first_sweep_start']
