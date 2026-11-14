"""Capture assessment only: no hardware, DSP processing, retries or file writes."""

from loguru import logger

from .base import CaptureContext, CaptureIssue

def stream_health(context: CaptureContext) -> CaptureIssue | None:
    """Inspect stream_status for underflow/overflow flags indicating dropout.

    Returns an issue if the capture is unhealthy (needs retry), None if clean.
    """
    result = context.capture
    stream_status = result['capture_metadata'].get('stream_status', [])
    for entry in stream_status:
        status_str = entry.get('status', '')
        if 'Underflow' in status_str or 'Overflow' in status_str:
            logger.warning(
                f"Audio stream reported {status_str} at sample {entry.get('sample', 'unknown')}; "
                f"stream entry={entry}")
            return CaptureIssue('stream_health', 'stream health rejection (underflow/overflow)')
    return None

