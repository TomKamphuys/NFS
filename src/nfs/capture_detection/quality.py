"""Capture assessment only: no hardware, DSP processing, retries or file writes."""

from .base import CaptureContext, CaptureIssue

def psr_quality(context: CaptureContext) -> CaptureIssue | None:
    """Check post-deconvolution PSR against the quality threshold.

    PSR reflects loopback marker alignment quality — a dropout corrupts
    the marker alignment, lowering PSR. SNR is intentionally excluded
    because the mic can legitimately have low SNR at certain positions.

    Returns an issue if quality is below threshold (needs retry), None if good.
    """
    metrics = context.metrics
    assert metrics is not None
    psr_threshold = context.settings.get('retry_psr_threshold', 3.0)
    psr = metrics.get('psr', 0.0)
    if psr < psr_threshold:
        return CaptureIssue('psr_quality',
                            f"quality below threshold (PSR={psr:.2f}, threshold={psr_threshold:.2f})")
    return None

def thd_quality(context: CaptureContext) -> CaptureIssue | None:
    """Check post-deconvolution THD against the distortion threshold.

    On a loopback setup, high THD indicates a transient problem (clipping,
    mechanical anomaly, etc.) rather than a property of the DUT.

    Returns an issue if THD exceeds threshold (needs retry), None if good.
    """
    metrics = context.metrics
    assert metrics is not None
    thd_threshold = context.settings.get('retry_thd_threshold', 10.0)
    thd = metrics.get('thd_pct', 0.0)
    if thd > thd_threshold:
        return CaptureIssue('thd_quality',
                            f"high distortion (THD={thd:.2f}%, threshold={thd_threshold:.2f}%)")
    return None

