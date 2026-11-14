"""Swappable capture detectors, independent of audio capture and DSP processing."""

from .base import (CaptureContext, CaptureDetector, CaptureIssue, DetectorSession,
                   DetectorPreparationCancelled, find_issue, prepare_detectors, archive_detector_state)
from .events import DetectorDecision, DetectorEvent
from .loopback_gaps import loopback_gaps
from .microphone_gaps import microphone_gaps
from .quality import psr_quality, thd_quality
from .stream_health import stream_health
from .loopback_reference import (loopback_reference, prepare_loopback_reference,
                                 archive_loopback_reference)

# Order preserves the existing first-rejection behaviour. The GUI and config
# loader use this same registry so every built-in detector has an enable switch.
DEFAULT_DETECTORS = (
    CaptureDetector("stream_health", "raw", stream_health, "retry_check_stream_health",
                    "Check stream health",
                    "Reject captures when the driver reports buffer underflow or overflow."),
    CaptureDetector("loopback_gaps", "raw", loopback_gaps, "retry_check_loopback_silence",
                    "Check loopback silence",
                    "Reject captures with silence gaps in the electrical loopback."),
    CaptureDetector("microphone_gaps", "raw", microphone_gaps, "retry_check_mic_silence",
                    "Check mic silence",
                    "Reject captures with sudden energy drops in the microphone recording."),
    CaptureDetector("psr_quality", "metrics", psr_quality, "retry_check_psr",
                    "Check PSR quality",
                    "Reject captures below the configured loopback marker PSR threshold."),
    CaptureDetector("thd_quality", "metrics", thd_quality, "retry_check_thd",
                    "Check THD distortion",
                    "Reject captures above the configured THD threshold."),
    CaptureDetector("loopback_reference", "raw", loopback_reference, "retry_check_loopback_reference",
                    "Check golden loopback reference",
                    "Experimental: before each measurement set, capture three single-sweep sequences, "
                    "and require them to agree. Compare every later repeat's "
                    "loopback to their fixed median using a frequency-dependent tolerance. "
                    "Applies to measurement sets only.",
                    default_enabled=False, group="Experimental reference detector",
                    prepare=prepare_loopback_reference, archive=archive_loopback_reference,
                    preparation_failure_message='The three reference loopbacks do not match.',
                    setup_hint='Review Audio Setup.'),
)
