"""Swappable capture detectors, independent of audio capture and DSP processing."""

from .base import CaptureContext, CaptureDetector, CaptureIssue, find_issue
from .loopback_gaps import loopback_gaps
from .microphone_gaps import microphone_gaps
from .quality import psr_quality, thd_quality
from .stream_health import stream_health

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
)
