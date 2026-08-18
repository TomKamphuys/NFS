"""Headless electrical-loopback diagnostics for the real-time audio path.

The module deliberately keeps analysis and file writing out of PortAudio's
callback.  It is used by the Qt launcher's setup wizard, but the actual test
runner does not construct a QApplication.
"""

from __future__ import annotations

import configparser
import json
import math
import platform
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf
from scipy import signal


DIAGNOSTIC_VERSION = 4
DEFAULT_LEVEL_DBFS = -30.0
DEFAULT_TRIAL_SECONDS = 4.0
TRANSITION_TRIAL_SECONDS = 8.0
STARTUP_REPORT_SECONDS = 1.5
ONSET_GUARD_SECONDS = 0.25


@dataclass
class DiagnosticRequest:
    config_path: str
    input_device_name: str
    input_hostapi: str
    output_device_name: str
    output_hostapi: str
    input_channel: int
    output_channel: int
    sample_rate: int
    blocksize: int
    output_root: str
    suite: str = "standard"


@dataclass
class CallbackTrace:
    stream_open_request_ns: int = 0
    stream_closed_ns: int = 0
    monotonic_ns: list[int] = field(default_factory=list)
    frames: list[int] = field(default_factory=list)
    status: list[str] = field(default_factory=list)
    input_adc_time: list[float] = field(default_factory=list)
    current_time: list[float] = field(default_factory=list)
    output_dac_time: list[float] = field(default_factory=list)
    trace_overflow_count: int = 0


@dataclass
class TrialResult:
    name: str
    engine: str
    scenario: str
    sample_rate: int
    requested_blocksize: int
    callback_frames: dict[str, int]
    callback_statuses: dict[str, int]
    elapsed_seconds: float
    expected_seconds: float
    analysis: dict[str, Any]
    capture_file: str | None = None
    error: str | None = None


class CoarseProgress:
    """Print progress between trials, never from inside an audio callback."""

    def __init__(self, total: int, step_percent: int = 5, initial_done: int = 0) -> None:
        self.total = max(1, int(total))
        self.done = max(0, min(self.total, int(initial_done)))
        self.step_percent = max(1, int(step_percent))
        self._last_bucket = -1
        self.report("Preparing")

    def report(self, label: str) -> None:
        percent = min(100, int(round(self.done * 100 / self.total)))
        bucket = percent // self.step_percent
        if bucket != self._last_bucket or percent == 100:
            print(
                f"Audio diagnostic: {percent:3d}% ({self.done}/{self.total}) - {label}",
                flush=True,
            )
            self._last_bucket = bucket

    def complete(self, label: str) -> None:
        self.done = min(self.total, self.done + 1)
        self.report(label)


class CallbackRecorder:
    """Preallocated callback recorder shared by direct and backend trials."""

    def __init__(self, capacity_frames: int) -> None:
        self.capacity = max(1, int(capacity_frames))
        self.input = np.zeros(self.capacity, dtype=np.float32)
        self.output = np.zeros(self.capacity, dtype=np.float32)
        self.position = 0
        trace_capacity = min(self.capacity, max(2048, self.capacity // 16 + 1024))
        self._trace_monotonic = np.zeros(trace_capacity, dtype=np.int64)
        self._trace_frames = np.zeros(trace_capacity, dtype=np.int32)
        self._trace_status = np.zeros(trace_capacity, dtype=np.uint8)
        self._trace_input_time = np.zeros(trace_capacity, dtype=np.float64)
        self._trace_current_time = np.zeros(trace_capacity, dtype=np.float64)
        self._trace_output_time = np.zeros(trace_capacity, dtype=np.float64)
        self._trace_position = 0
        self._trace_overflow = 0

    def record(
        self,
        indata: np.ndarray,
        outdata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
        *,
        input_column: int,
        output_column: int,
    ) -> None:
        n = min(int(frames), self.capacity - self.position)
        if n > 0:
            self.input[self.position : self.position + n] = indata[:n, input_column]
            self.output[self.position : self.position + n] = outdata[:n, output_column]
            self.position += n
        trace_index = self._trace_position
        if trace_index < len(self._trace_frames):
            status_code = 0
            if status:
                status_code |= int(bool(getattr(status, "input_underflow", False))) << 0
                status_code |= int(bool(getattr(status, "input_overflow", False))) << 1
                status_code |= int(bool(getattr(status, "output_underflow", False))) << 2
                status_code |= int(bool(getattr(status, "output_overflow", False))) << 3
                status_code |= int(bool(getattr(status, "priming_output", False))) << 4
            self._trace_monotonic[trace_index] = time.monotonic_ns()
            self._trace_frames[trace_index] = int(frames)
            self._trace_status[trace_index] = status_code
            self._trace_input_time[trace_index] = float(getattr(time_info, "inputBufferAdcTime", 0.0))
            self._trace_current_time[trace_index] = float(getattr(time_info, "currentTime", 0.0))
            self._trace_output_time[trace_index] = float(getattr(time_info, "outputBufferDacTime", 0.0))
            self._trace_position += 1
        else:
            self._trace_overflow += 1

    def arrays(self) -> tuple[np.ndarray, np.ndarray]:
        return self.output[: self.position].copy(), self.input[: self.position].copy()

    @property
    def trace(self) -> CallbackTrace:
        count = self._trace_position

        def status_text(code: int) -> str:
            names = []
            for bit, name in enumerate(
                ("input_underflow", "input_overflow", "output_underflow", "output_overflow", "priming_output")
            ):
                if code & (1 << bit):
                    names.append(name)
            return ",".join(names)

        return CallbackTrace(
            monotonic_ns=self._trace_monotonic[:count].tolist(),
            frames=self._trace_frames[:count].tolist(),
            status=[status_text(int(value)) for value in self._trace_status[:count]],
            input_adc_time=self._trace_input_time[:count].tolist(),
            current_time=self._trace_current_time[:count].tolist(),
            output_dac_time=self._trace_output_time[:count].tolist(),
            trace_overflow_count=self._trace_overflow,
        )


def _read_audio_config(config_path: str | Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.read(str(config_path))
    if not parser.has_section("audio"):
        raise ValueError(f"No [audio] section was found in {config_path}")
    return parser


def request_from_config(config_path: str | Path, output_root: str | Path) -> DiagnosticRequest:
    parser = _read_audio_config(config_path)
    audio = parser["audio"]
    return DiagnosticRequest(
        config_path=str(Path(config_path).resolve()),
        input_device_name=audio.get("in_dev_name", "").strip(),
        input_hostapi=audio.get("in_dev_hostapi", "").strip(),
        output_device_name=audio.get("out_dev_name", "").strip(),
        output_hostapi=audio.get("out_dev_hostapi", "").strip(),
        input_channel=audio.getint("in_ch_loop", fallback=1),
        output_channel=audio.getint("out_ch_ref", fallback=1),
        sample_rate=audio.getint("fs", fallback=48000),
        blocksize=audio.getint("blocksize", fallback=0),
        output_root=str(Path(output_root).resolve()),
    )


def write_request(request: DiagnosticRequest, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(request), indent=2), encoding="utf-8")
    return target


def read_request(path: str | Path) -> DiagnosticRequest:
    return DiagnosticRequest(**json.loads(Path(path).read_text(encoding="utf-8")))


def generate_probe(
    sample_rate: int,
    duration_seconds: float,
    *,
    seed: int,
    level_dbfs: float = DEFAULT_LEVEL_DBFS,
    pilot_hz: float = 997.0,
) -> np.ndarray:
    """Create a deterministic, correlation-friendly and audibly benign probe."""

    count = int(round(sample_rate * duration_seconds))
    t = np.arange(count, dtype=np.float64) / float(sample_rate)
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(count)
    sos = signal.butter(4, [80.0, min(15000.0, sample_rate * 0.42)], btype="bandpass", fs=sample_rate, output="sos")
    noise = signal.sosfilt(sos, noise)
    noise /= np.max(np.abs(noise)) + 1e-12
    probe = (
        0.30 * np.sin(2.0 * np.pi * pilot_hz * t)
        + 0.10 * np.sin(2.0 * np.pi * 3109.0 * t + 0.37)
        + 0.60 * noise
    )
    peak = np.max(np.abs(probe)) + 1e-12
    probe *= (10.0 ** (level_dbfs / 20.0)) / peak
    fade = min(count // 2, max(1, int(round(sample_rate * 0.01))))
    ramp = np.sin(np.linspace(0.0, math.pi / 2.0, fade)) ** 2
    probe[:fade] *= ramp
    probe[-fade:] *= ramp[::-1]
    return probe.astype(np.float32)


def _aligned_arrays(expected: np.ndarray, captured: np.ndarray, lag: int) -> tuple[np.ndarray, np.ndarray]:
    if lag >= 0:
        length = min(len(expected), len(captured) - lag)
        return expected[: max(0, length)], captured[lag : lag + max(0, length)]
    offset = -lag
    length = min(len(expected) - offset, len(captured))
    return expected[offset : offset + max(0, length)], captured[: max(0, length)]


def _normalized_correlation(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    a64 = np.asarray(a, dtype=np.float64)
    b64 = np.asarray(b, dtype=np.float64)
    a64 -= np.mean(a64)
    b64 -= np.mean(b64)
    denom = float(np.linalg.norm(a64) * np.linalg.norm(b64))
    return float(np.dot(a64, b64) / denom) if denom > 1e-20 else 0.0


def analyze_capture(
    expected: np.ndarray,
    captured: np.ndarray,
    sample_rate: int,
    *,
    startup_seconds: float = STARTUP_REPORT_SECONDS,
) -> dict[str, Any]:
    """Estimate latency changes, interruptions, drift and residual error offline."""

    expected = np.asarray(expected, dtype=np.float32)
    captured = np.asarray(captured, dtype=np.float32)
    nonfinite_samples = int(np.count_nonzero(~np.isfinite(captured)))
    if nonfinite_samples:
        captured = np.nan_to_num(captured, nan=0.0, posinf=0.0, neginf=0.0)
    if not np.all(np.isfinite(expected)):
        expected = np.nan_to_num(expected, nan=0.0, posinf=0.0, neginf=0.0)
    if len(expected) < 32 or len(captured) < 32:
        return {"passed": False, "reason": "capture_too_short", "captured_frames": int(len(captured))}

    onset_block = max(1, sample_rate // 100)
    onset_count = len(captured) // onset_block
    if onset_count:
        onset_view = captured[: onset_count * onset_block].reshape(onset_count, onset_block)
        onset_rms = np.sqrt(np.mean(onset_view.astype(np.float64) ** 2, axis=1))
        onset_threshold = max(1e-6, float(np.max(onset_rms)) * 0.1)
        onset_indices = np.flatnonzero(onset_rms >= onset_threshold)
        onset_frame = int(onset_indices[0] * onset_block) if len(onset_indices) else None
    else:
        onset_frame = None

    expected_zero = expected.astype(np.float64) - float(np.mean(expected))
    captured_zero = captured.astype(np.float64) - float(np.mean(captured))
    corr = signal.correlate(captured_zero, expected_zero, mode="full", method="fft")
    lags = signal.correlation_lags(len(captured_zero), len(expected_zero), mode="full")
    max_lag = max(1, int(round(sample_rate * 0.5)))
    keep = np.abs(lags) <= max_lag
    restricted = corr[keep]
    restricted_lags = lags[keep]
    global_peak_index = int(np.argmax(np.abs(restricted)))
    global_lag = int(restricted_lags[global_peak_index])
    correlation_polarity = 1.0 if restricted[global_peak_index] >= 0.0 else -1.0

    aligned_expected, aligned_captured = _aligned_arrays(expected, captured, global_lag)
    if len(aligned_expected) < 32:
        return {"passed": False, "reason": "could_not_align", "global_lag_frames": global_lag}

    minimum_start = int(round(startup_seconds * sample_rate))
    onset_in_aligned_capture = None if onset_frame is None else max(0, onset_frame - max(0, global_lag))
    onset_guard = int(round(ONSET_GUARD_SECONDS * sample_rate))
    analysis_start = max(
        minimum_start,
        0 if onset_in_aligned_capture is None else onset_in_aligned_capture + onset_guard,
    )
    analysis_start = min(len(aligned_expected) - 1, analysis_start)
    steady_expected = aligned_expected[analysis_start:]
    steady_captured = aligned_captured[analysis_start:]
    gain_den = float(np.dot(steady_expected, steady_expected)) + 1e-20
    gain = float(np.dot(steady_expected, steady_captured) / gain_den)
    residual = steady_captured - gain * steady_expected
    signal_rms = float(np.sqrt(np.mean((gain * steady_expected) ** 2)))
    residual_rms = float(np.sqrt(np.mean(residual**2)))
    snr_db = 20.0 * math.log10((signal_rms + 1e-20) / (residual_rms + 1e-20))

    window = max(256, int(round(sample_rate * 0.1)))
    step = window
    search_radius = max(64, int(round(sample_rate * 0.025)))
    local_lags: list[int] = []
    local_corrs: list[float] = []
    local_rms: list[float] = []
    event_frames: list[int] = []
    start = analysis_start
    while start + window <= len(expected):
        ref = expected[start : start + window]
        predicted = start + global_lag
        if predicted < 0 or predicted + window > len(captured):
            break
        search_start = max(0, predicted - search_radius)
        search_end = min(len(captured), predicted + window + search_radius)
        area = captured[search_start:search_end]
        if len(area) < window:
            break
        values = signal.correlate(area, ref, mode="valid", method="fft")
        pos = int(np.argmax(values * correlation_polarity))
        lag = int(search_start + pos - start)
        candidate = captured[search_start + pos : search_start + pos + window]
        local_lags.append(lag)
        local_corrs.append(_normalized_correlation(ref, candidate))
        local_rms.append(float(np.sqrt(np.mean(candidate.astype(np.float64) ** 2))))
        start += step

    lag_jumps: list[int] = []
    for index, delta in enumerate(np.diff(local_lags)):
        if abs(int(delta)) >= 2:
            lag_jumps.append(int(delta))
            event_frames.append(analysis_start + (index + 1) * step)

    median_rms = float(np.median(local_rms)) if local_rms else 0.0
    dropout_windows = [
        index
        for index, value in enumerate(local_rms)
        if median_rms > 1e-9 and value < median_rms * 0.25
    ]
    event_frames.extend(analysis_start + index * step for index in dropout_windows)
    event_frames = sorted(set(int(value) for value in event_frames))

    event_intervals = np.diff(event_frames) if len(event_frames) >= 2 else np.array([], dtype=np.int64)
    interval_median = float(np.median(event_intervals)) if len(event_intervals) else None
    interval_cv = (
        float(np.std(event_intervals) / (np.mean(event_intervals) + 1e-20))
        if len(event_intervals) >= 2
        else None
    )

    corr_median = float(np.median(local_corrs)) if local_corrs else 0.0
    corr_p05 = float(np.percentile(local_corrs, 5)) if local_corrs else 0.0
    lag_span = int(max(local_lags) - min(local_lags)) if local_lags else 0
    estimated_net_skip = int(local_lags[-1] - local_lags[0]) if len(local_lags) >= 2 else 0
    clock_drift_ppm = (
        float(estimated_net_skip * 1_000_000.0 / max(1, (len(local_lags) - 1) * step))
        if len(local_lags) >= 2
        else 0.0
    )
    passed = bool(
        nonfinite_samples == 0
        and signal_rms > 1e-6
        and corr_median >= 0.90
        and corr_p05 >= 0.55
        and not dropout_windows
        and not lag_jumps
    )
    return {
        "passed": passed,
        "captured_frames": int(len(captured)),
        "expected_frames": int(len(expected)),
        "nonfinite_sample_count": nonfinite_samples,
        "global_lag_frames": global_lag,
        "global_lag_ms": global_lag * 1000.0 / sample_rate,
        "minimum_startup_excluded_seconds": float(startup_seconds),
        "signal_onset_frame": onset_frame,
        "signal_onset_ms": None if onset_frame is None else onset_frame * 1000.0 / sample_rate,
        "startup_delay_warning": bool(
            onset_frame is not None and onset_frame > int(round(startup_seconds * sample_rate))
        ),
        "steady_analysis_start_frame": int(analysis_start),
        "steady_analysis_start_seconds": analysis_start / sample_rate,
        "steady_analyzed_seconds": len(steady_expected) / sample_rate,
        "gain_db": 20.0 * math.log10(abs(gain) + 1e-20),
        "steady_snr_db": snr_db,
        "window_correlation_median": corr_median,
        "window_correlation_p05": corr_p05,
        "lag_span_frames": lag_span,
        "lag_jump_frames": lag_jumps,
        "estimated_net_skipped_frames": estimated_net_skip,
        "clock_drift_ppm": clock_drift_ppm,
        "dropout_window_indices": dropout_windows,
        "event_frames": event_frames,
        "event_interval_median_frames": interval_median,
        "event_interval_cv": interval_cv,
        "local_lags": local_lags,
        "local_correlations": local_corrs,
    }


def analyze_sine_capture(
    expected: np.ndarray,
    captured: np.ndarray,
    sample_rate: int,
    *,
    frequency: float = 997.0,
    startup_seconds: float = STARTUP_REPORT_SECONDS,
) -> dict[str, Any]:
    """Analyze a production sine without ambiguous whole-period lag matching."""

    expected = np.asarray(expected, dtype=np.float32)
    captured = np.asarray(captured, dtype=np.float32)
    nonfinite_samples = int(np.count_nonzero(~np.isfinite(captured)))
    if nonfinite_samples:
        captured = np.nan_to_num(captured, nan=0.0, posinf=0.0, neginf=0.0)
    onset_block = max(1, sample_rate // 100)
    onset_count = len(captured) // onset_block
    if onset_count:
        onset_view = captured[: onset_count * onset_block].reshape(onset_count, onset_block)
        onset_rms = np.sqrt(np.mean(onset_view.astype(np.float64) ** 2, axis=1))
        onset_threshold = max(1e-6, float(np.max(onset_rms)) * 0.1)
        onset_indices = np.flatnonzero(onset_rms >= onset_threshold)
        onset_frame = int(onset_indices[0] * onset_block) if len(onset_indices) else None
    else:
        onset_frame = None
    start = max(
        int(round(startup_seconds * sample_rate)),
        0
        if onset_frame is None
        else onset_frame + int(round(ONSET_GUARD_SECONDS * sample_rate)),
    )
    start = min(len(captured), start)
    captured = captured[start:]
    expected = expected[start : start + len(captured)]
    window = max(256, int(round(sample_rate * 0.1)))
    count = min(len(captured), len(expected)) // window
    if count < 3:
        return {
            "passed": False,
            "reason": "capture_too_short",
            "captured_frames": int(len(captured)),
            "nonfinite_sample_count": nonfinite_samples,
        }

    amplitudes: list[float] = []
    phases: list[float] = []
    residual_ratios: list[float] = []
    for index in range(count):
        begin = index * window
        end = begin + window
        samples = captured[begin:end].astype(np.float64)
        absolute_n = np.arange(begin + start, end + start, dtype=np.float64)
        carrier = np.exp(-2j * np.pi * frequency * absolute_n / sample_rate)
        coefficient = 2.0 * np.mean(samples * carrier)
        fitted = np.real(coefficient * np.exp(2j * np.pi * frequency * absolute_n / sample_rate))
        sample_rms = float(np.sqrt(np.mean(samples**2)))
        residual_rms = float(np.sqrt(np.mean((samples - fitted) ** 2)))
        amplitudes.append(float(abs(coefficient)))
        phases.append(float(np.angle(coefficient)))
        residual_ratios.append(residual_rms / (sample_rms + 1e-20))

    phase = np.unwrap(np.asarray(phases, dtype=np.float64))
    phase_steps = np.diff(phase)
    typical_step = float(np.median(phase_steps)) if len(phase_steps) else 0.0
    phase_step_error = phase_steps - typical_step
    phase_events = np.flatnonzero(np.abs(phase_step_error) > 0.20).astype(int).tolist()
    amplitude = np.asarray(amplitudes, dtype=np.float64)
    median_amplitude = float(np.median(amplitude))
    amplitude_deviation = np.abs(amplitude - median_amplitude) / (median_amplitude + 1e-20)
    amplitude_events = np.flatnonzero(amplitude_deviation > 0.20).astype(int).tolist()
    residual = np.asarray(residual_ratios, dtype=np.float64)
    frequency_offset_hz = typical_step / (2.0 * np.pi * (window / sample_rate))

    passed = bool(
        nonfinite_samples == 0
        and median_amplitude > 1e-5
        and float(np.median(residual)) < 0.05
        and float(np.percentile(residual, 95)) < 0.15
        and not phase_events
        and not amplitude_events
    )
    return {
        "passed": passed,
        "analysis_kind": "sine_phase",
        "captured_frames": int(len(captured)),
        "expected_frames": int(len(expected)),
        "nonfinite_sample_count": nonfinite_samples,
        "minimum_startup_excluded_seconds": float(startup_seconds),
        "signal_onset_frame": onset_frame,
        "signal_onset_ms": None if onset_frame is None else onset_frame * 1000.0 / sample_rate,
        "startup_delay_warning": bool(
            onset_frame is not None and onset_frame > int(round(startup_seconds * sample_rate))
        ),
        "steady_analysis_start_frame": int(start),
        "steady_analysis_start_seconds": start / sample_rate,
        "steady_analyzed_seconds": len(captured) / sample_rate,
        "tone_frequency_hz": float(frequency),
        "tone_amplitude_median": median_amplitude,
        "tone_amplitude_deviation_p95": float(np.percentile(amplitude_deviation, 95)),
        "sine_fit_residual_median": float(np.median(residual)),
        "sine_fit_residual_p95": float(np.percentile(residual, 95)),
        "phase_step_typical_radians": typical_step,
        "phase_step_error_p95_radians": float(np.percentile(np.abs(phase_step_error), 95)),
        "phase_event_window_indices": phase_events,
        "amplitude_event_window_indices": amplitude_events,
        "estimated_frequency_offset_hz": frequency_offset_hz,
    }


def _status_counter(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(value for value in values if value).items()))


def _frame_counter(values: Iterable[int]) -> dict[str, int]:
    return {str(key): value for key, value in sorted(Counter(int(v) for v in values).items())}


def _trace_metrics(trace: CallbackTrace, sample_rate: int) -> dict[str, Any]:
    if len(trace.monotonic_ns) < 2:
        return {
            "callback_count": len(trace.monotonic_ns),
            "late_callback_indices": [],
            "trace_overflow_count": trace.trace_overflow_count,
        }
    actual = np.diff(np.asarray(trace.monotonic_ns, dtype=np.float64)) / 1e9
    expected = np.asarray(trace.frames[:-1], dtype=np.float64) / float(sample_rate)
    error_ms = (actual - expected) * 1000.0
    tolerance = np.maximum(0.001, expected * 0.5)
    late = np.flatnonzero(actual - expected > tolerance).astype(int).tolist()
    span_seconds = float((trace.monotonic_ns[-1] - trace.monotonic_ns[0]) / 1e9)
    frames_during_span = int(sum(trace.frames[:-1]))
    callback_frame_rate = frames_during_span / span_seconds if span_seconds > 0.0 else 0.0
    return {
        "callback_count": len(trace.monotonic_ns),
        "interval_error_median_ms": float(np.median(error_ms)),
        "interval_error_p95_ms": float(np.percentile(error_ms, 95)),
        "interval_error_max_ms": float(np.max(error_ms)),
        "late_callback_indices": late,
        "callback_span_seconds": span_seconds,
        "frames_during_callback_span": frames_during_span,
        "effective_callback_frame_rate": callback_frame_rate,
        "effective_to_requested_rate_ratio": callback_frame_rate / sample_rate,
        "trace_overflow_count": trace.trace_overflow_count,
    }


def _resolve_devices(request: DiagnosticRequest):
    from nfs.audio import get_devices_and_channels

    catalog = get_devices_and_channels()

    def find(name: str, api: str, capability: str) -> int:
        channel_key = "input_channels" if capability == "input" else "output_channels"
        matches = [
            dev_id
            for dev_id, info in catalog.items()
            if str(info.get("name", "")).casefold() == name.casefold()
            and str(info.get("hostapi", "")).casefold() == api.casefold()
            and info.get(channel_key)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Could not uniquely resolve {capability} device '{name}' on '{api}'. "
                "Open Audio Setup, save the intended device, and retry."
            )
        return int(matches[0])

    return find(request.input_device_name, request.input_hostapi, "input"), find(
        request.output_device_name, request.output_hostapi, "output"
    ), catalog


def _stream_layout(request: DiagnosticRequest, in_dev: int, out_dev: int, catalog: dict[int, dict[str, Any]]):
    import sounddevice as sd

    use_asio_in = "ASIO" in str(catalog[in_dev]["hostapi"]).upper()
    use_asio_out = "ASIO" in str(catalog[out_dev]["hostapi"]).upper()
    if use_asio_in:
        in_channels = 1
        in_column = 0
        in_extra = sd.AsioSettings(channel_selectors=[request.input_channel])
    else:
        in_channels = request.input_channel + 1
        in_column = request.input_channel
        in_extra = None
    if use_asio_out:
        out_channels = 1
        out_column = 0
        out_extra = sd.AsioSettings(channel_selectors=[request.output_channel])
    else:
        out_channels = request.output_channel + 1
        out_column = request.output_channel
        out_extra = None
    return in_channels, out_channels, in_column, out_column, in_extra, out_extra


def _direct_trial(
    request: DiagnosticRequest,
    in_dev: int,
    out_dev: int,
    catalog: dict[int, dict[str, Any]],
    *,
    sample_rate: int,
    blocksize: int,
    name: str,
    scenario: str,
    probes: list[np.ndarray],
    defer_analysis: bool = False,
) -> tuple[TrialResult, np.ndarray, np.ndarray, CallbackTrace]:
    import sounddevice as sd

    expected = np.concatenate(probes)
    recorder = CallbackRecorder(len(expected) + max(sample_rate, max(1, blocksize) * 4))
    in_channels, out_channels, in_column, out_column, in_extra, out_extra = _stream_layout(
        request, in_dev, out_dev, catalog
    )
    play_pos = 0
    done = threading.Event()

    def callback(indata, outdata, frames, time_info, status):
        nonlocal play_pos
        outdata.fill(0)
        count = min(int(frames), len(expected) - play_pos)
        if count > 0:
            outdata[:count, out_column] = expected[play_pos : play_pos + count]
        recorder.record(
            indata,
            outdata,
            frames,
            time_info,
            status,
            input_column=in_column,
            output_column=out_column,
        )
        play_pos += count
        if play_pos >= len(expected):
            done.set()

    started = time.monotonic()
    stream_open_request_ns = 0
    error = None
    try:
        stream_open_request_ns = time.monotonic_ns()
        with sd.Stream(
            device=(in_dev, out_dev),
            samplerate=sample_rate,
            blocksize=blocksize,
            dtype="float32",
            channels=(in_channels, out_channels),
            extra_settings=(in_extra, out_extra),
            dither_off=True,
            callback=callback,
        ):
            if not done.wait(len(expected) / sample_rate * 3.0 + 10.0):
                raise TimeoutError("Audio callbacks did not complete in the expected time")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    stream_closed_ns = time.monotonic_ns()
    elapsed = time.monotonic() - started
    actual_output, captured = recorder.arrays()
    reference = actual_output[: min(len(expected), len(actual_output))]
    recorded = captured[: len(reference)]
    trace = recorder.trace
    trace.stream_open_request_ns = stream_open_request_ns
    trace.stream_closed_ns = stream_closed_ns
    status_counts = _status_counter(trace.status)
    result = TrialResult(
        name=name,
        engine="direct",
        scenario=scenario,
        sample_rate=sample_rate,
        requested_blocksize=blocksize,
        callback_frames=_frame_counter(trace.frames),
        callback_statuses=status_counts,
        elapsed_seconds=elapsed,
        expected_seconds=len(expected) / sample_rate,
        analysis={"analysis_deferred": True},
        error=error,
    )
    payload = (result, reference, recorded, trace)
    return payload if defer_analysis else _finish_direct_analysis(payload)


def _finish_direct_analysis(
    payload: tuple[TrialResult, np.ndarray, np.ndarray, CallbackTrace],
) -> tuple[TrialResult, np.ndarray, np.ndarray, CallbackTrace]:
    """Perform direct-stream analysis after timing-sensitive stream work."""

    result, reference, recorded, trace = payload
    analysis = (
        analyze_capture(reference, recorded, result.sample_rate)
        if result.error is None
        else {"passed": False, "reason": "stream_error"}
    )
    timing = _trace_metrics(trace, result.sample_rate)
    analysis["callback_timing"] = timing
    rate_ratio = float(timing.get("effective_to_requested_rate_ratio", 1.0))
    if not 0.90 <= rate_ratio <= 1.10:
        analysis["passed"] = False
        analysis["callback_rate_failure"] = True
    if result.callback_statuses:
        analysis["passed"] = False
        analysis["callback_status_failure"] = True
    result.analysis = analysis
    return payload


def _temporary_config(
    original: str | Path,
    target: Path,
    sample_rate: int,
    blocksize: int,
    request: DiagnosticRequest,
    in_dev: int,
    out_dev: int,
    catalog: dict[int, dict[str, Any]],
) -> Path:
    parser = _read_audio_config(original)
    audio = parser["audio"]
    audio["mode"] = "hardware"
    audio["fs"] = str(sample_rate)
    audio["blocksize"] = str(blocksize)
    audio["in_dev"] = str(in_dev)
    audio["out_dev"] = str(out_dev)
    audio["in_dev_name"] = request.input_device_name
    audio["in_dev_hostapi"] = request.input_hostapi
    audio["out_dev_name"] = request.output_device_name
    audio["out_dev_hostapi"] = request.output_hostapi
    audio["in_ch_loop"] = str(request.input_channel)
    audio["out_ch_ref"] = str(request.output_channel)
    input_options = [int(value) for value in catalog[in_dev].get("input_channels", [])]
    output_options = [int(value) for value in catalog[out_dev].get("output_channels", [])]
    other_input = next((value for value in input_options if value != request.input_channel), request.input_channel)
    other_output = next((value for value in output_options if value != request.output_channel), request.output_channel)
    audio["in_ch_mic"] = str(other_input)
    audio["out_ch_spkr"] = str(other_output)
    with target.open("w", encoding="utf-8") as handle:
        parser.write(handle)
    return target


def _backend_series(
    request: DiagnosticRequest,
    in_dev: int,
    out_dev: int,
    catalog: dict[int, dict[str, Any]],
    *,
    sample_rate: int,
    blocksize: int,
    run_dir: Path,
) -> list[tuple[TrialResult, np.ndarray, np.ndarray, CallbackTrace]]:
    """Run repeated play_sine calls on one production Audio object."""

    import nfs.audio as audio_module

    temp_config = _temporary_config(
        request.config_path,
        run_dir / "backend_series_config.ini",
        sample_rate,
        blocksize,
        request,
        in_dev,
        out_dev,
        catalog,
    )
    original_stream = audio_module.sd.Stream
    active_recorder: CallbackRecorder | None = None
    use_asio_in = "ASIO" in request.input_hostapi.upper()
    use_asio_out = "ASIO" in request.output_hostapi.upper()
    input_column = 0 if use_asio_in else request.input_channel
    # Production ASIO maps speaker to column 0 and reference to column 1.
    output_column = 1 if use_asio_out else request.output_channel

    class ObservedStream:
        def __init__(self, *args, **kwargs):
            original_callback = kwargs["callback"]

            def observed(indata, outdata, frames, time_info, status):
                original_callback(indata, outdata, frames, time_info, status)
                recorder = active_recorder
                if recorder is not None:
                    recorder.record(
                        indata,
                        outdata,
                        frames,
                        time_info,
                        status,
                        input_column=input_column,
                        output_column=output_column,
                    )

            kwargs["callback"] = observed
            self._stream = original_stream(*args, **kwargs)

        def __enter__(self):
            self._stream.start()
            return self

        def __exit__(self, exc_type, exc, tb):
            try:
                self._stream.stop()
            finally:
                self._stream.close()
            return False

        def __getattr__(self, item):
            return getattr(self._stream, item)

    payloads: list[tuple[TrialResult, np.ndarray, np.ndarray, CallbackTrace]] = []
    audio = None
    try:
        audio_module.sd.Stream = ObservedStream
        audio = audio_module.AudioFactory.create(str(temp_config))
        specs = [
            ("backend_persistent", "persistent", True, 12.0),
            ("backend_reopen_1", "complete_close_reopen_1", False, DEFAULT_TRIAL_SECONDS),
            ("backend_reopen_2", "complete_close_reopen_2", False, DEFAULT_TRIAL_SECONDS),
            ("backend_reopen_3", "complete_close_reopen_3", False, DEFAULT_TRIAL_SECONDS),
        ]
        for base_name, scenario, persistent, duration in specs:
            name = f"{base_name}_{sample_rate}_{blocksize}"
            active_recorder = CallbackRecorder(int(sample_rate * (duration + 3.0)))
            started = time.monotonic()
            error = None
            try:
                if persistent:
                    audio.play_sine(997.0, DEFAULT_LEVEL_DBFS, None)
                    time.sleep(duration)
                    audio.stop_sine()
                else:
                    audio.play_sine(997.0, DEFAULT_LEVEL_DBFS, duration)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                try:
                    audio.stop_sine()
                except Exception:
                    pass
            elapsed = time.monotonic() - started
            reference, recorded = active_recorder.arrays()
            trace = active_recorder.trace
            analysis = (
                analyze_sine_capture(reference, recorded, sample_rate, frequency=997.0)
                if error is None
                else {"passed": False, "reason": "stream_error"}
            )
            timing = _trace_metrics(trace, sample_rate)
            analysis["callback_timing"] = timing
            rate_ratio = float(timing.get("effective_to_requested_rate_ratio", 1.0))
            if not 0.90 <= rate_ratio <= 1.10:
                analysis["passed"] = False
                analysis["callback_rate_failure"] = True
            status_counts = _status_counter(trace.status)
            if status_counts:
                analysis["passed"] = False
                analysis["callback_status_failure"] = True
            payloads.append(
                (
                    TrialResult(
                        name=name,
                        engine="production_backend",
                        scenario=scenario,
                        sample_rate=sample_rate,
                        requested_blocksize=blocksize,
                        callback_frames=_frame_counter(trace.frames),
                        callback_statuses=status_counts,
                        elapsed_seconds=elapsed,
                        expected_seconds=duration,
                        analysis=analysis,
                        error=error,
                    ),
                    reference,
                    recorded,
                    trace,
                )
            )
    finally:
        if audio is not None:
            try:
                audio.stop_sine()
            except Exception:
                pass
        audio_module.sd.Stream = original_stream
        temp_config.unlink(missing_ok=True)
    return payloads


def _supported_rates(in_dev: int, out_dev: int, preferred: int) -> list[int]:
    import sounddevice as sd

    rates: list[int] = []
    for rate in dict.fromkeys([preferred, 44100, 48000, 96000]):
        try:
            sd.check_input_settings(device=in_dev, samplerate=rate)
            sd.check_output_settings(device=out_dev, samplerate=rate)
        except Exception:
            continue
        rates.append(int(rate))
    return rates or [preferred]


def _block_sizes(request: DiagnosticRequest) -> list[int]:
    return list(dict.fromkeys([0, request.blocksize, 128, 512, 2048]))


def _write_trial_artifacts(
    run_dir: Path,
    result: TrialResult,
    reference: np.ndarray,
    recorded: np.ndarray,
    trace: CallbackTrace,
) -> None:
    stem = result.name
    capture_path = run_dir / f"{stem}_capture.flac"
    if len(recorded):
        sf.write(capture_path, recorded, result.sample_rate, subtype="PCM_24")
        result.capture_file = capture_path.name
    np.savez_compressed(run_dir / f"{stem}_reference.npz", reference=reference.astype(np.float32))
    (run_dir / f"{stem}_trace.json").write_text(
        json.dumps(asdict(trace), separators=(",", ":")), encoding="utf-8"
    )
    (run_dir / f"{stem}_result.json").write_text(
        json.dumps(asdict(result), indent=2, allow_nan=False), encoding="utf-8"
    )


def _system_manifest(
    request: DiagnosticRequest,
    catalog: dict[int, dict[str, Any]],
    rates: list[int],
) -> dict[str, Any]:
    try:
        import sounddevice as sd

        portaudio_version = sd.get_portaudio_version()
    except Exception as exc:
        portaudio_version = f"Unavailable: {exc}"
    public_request = asdict(request)
    public_request["config_path"] = Path(request.config_path).name
    public_request.pop("output_root", None)
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(request.config_path).resolve().parent,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        ).stdout.strip() or None
    except Exception:
        commit = None
    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "created": datetime.now().astimezone().isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "portaudio": portaudio_version,
        "git_commit": commit,
        "request": public_request,
        "supported_rates_tested": rates,
        "device_catalog": catalog,
    }


def _zip_directory(run_dir: Path) -> Path:
    archive = run_dir.with_name(f"{run_dir.name}_SEND_THIS_FILE.zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(run_dir))
    return archive


def _save_group(run_dir: Path, group: str, results: list[TrialResult], **extra: Any) -> None:
    payload = {"group": group, "results": [asdict(item) for item in results], **extra}
    (run_dir / f"group_{group}.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8"
    )


def _loopback_check(
    recorded: np.ndarray,
    sample_rate: int,
    callback_timing: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None, str | None]:
    steady = recorded[min(len(recorded), int(sample_rate * 2.0)) :]
    rms = float(np.sqrt(np.mean(steady.astype(np.float64) ** 2))) if len(steady) else 0.0
    peak = float(np.max(np.abs(steady))) if len(steady) else 0.0
    level_dbfs = 20.0 * math.log10(rms + 1e-20)
    problem = None
    problem_kind = None
    timing = callback_timing or {}
    rate_ratio = float(timing.get("effective_to_requested_rate_ratio", 1.0))
    effective_rate = float(timing.get("effective_callback_frame_rate", sample_rate))
    if not 0.67 <= rate_ratio <= 1.5:
        problem_kind = "driver_timing_failure"
        problem = (
            f"The audio driver callback is not running in real time: it reported approximately "
            f"{effective_rate:,.0f} frames/second for a requested {sample_rate:,} frames/second "
            f"({rate_ratio:.1f}x). The electrical loopback level cannot be assessed reliably."
        )
    elif peak >= 0.99:
        problem_kind = "loopback_clipping"
        problem = (
            "The electrical-loopback input clipped during the first test. "
            "Turn the interface input gain down and run the diagnostic again."
        )
    elif level_dbfs < -70.0:
        problem_kind = "loopback_missing"
        problem = (
            "No usable electrical-loopback signal was detected after the two-second startup period. "
            "Check the physical cable, selected channels and input gain, then run the diagnostic again."
        )
    return (
        {
            "rms_dbfs": level_dbfs,
            "peak": peak,
            "passed": problem is None,
            "problem_kind": problem_kind,
            "message": problem,
            "effective_callback_frame_rate": effective_rate,
            "effective_to_requested_rate_ratio": rate_ratio,
        },
        problem,
        problem_kind,
    )


def run_diagnostic_group(
    request_path: str | Path,
    run_dir: str | Path,
    group: str,
    *,
    progress_offset: int = 0,
    progress_total: int = 1,
) -> None:
    """Run one audio group in a fresh process and write its machine-readable result."""

    request = read_request(request_path)
    run_dir = Path(run_dir)
    results: list[TrialResult] = []
    progress = CoarseProgress(progress_total, initial_done=progress_offset)
    in_dev, out_dev, catalog = _resolve_devices(request)

    if group == "backend":
        # Do not probe alternate formats before this series. Some ASIO drivers
        # may initialize while answering format queries, which would spoil the
        # application's clean first-start evidence.
        payloads = _backend_series(
            request,
            in_dev,
            out_dev,
            catalog,
            sample_rate=request.sample_rate,
            blocksize=request.blocksize,
            run_dir=run_dir,
        )
        for payload in payloads:
            results.append(payload[0])
            _write_trial_artifacts(run_dir, *payload)
            progress.complete(payload[0].name)
        first_timing = payloads[0][0].analysis.get("callback_timing", {})
        check, setup_problem, problem_kind = _loopback_check(
            payloads[0][2], request.sample_rate, first_timing
        )
        (run_dir / "loopback_check.json").write_text(json.dumps(check, indent=2), encoding="utf-8")
        if setup_problem:
            problem_filename = (
                "DRIVER_TIMING_PROBLEM.txt"
                if problem_kind == "driver_timing_failure"
                else "SETUP_PROBLEM.txt"
            )
            (run_dir / problem_filename).write_text(setup_problem, encoding="utf-8")
        rates = _supported_rates(in_dev, out_dev, request.sample_rate)
        (run_dir / "manifest.json").write_text(
            json.dumps(_system_manifest(request, catalog, rates), indent=2, default=str), encoding="utf-8"
        )
        _save_group(
            run_dir,
            group,
            results,
            setup_problem=setup_problem,
            problem_kind=problem_kind,
            supported_rates=rates,
        )
        return

    if group == "direct_smoke":
        name = f"direct_smoke_{request.sample_rate}_{request.blocksize}"
        payload = _direct_trial(
            request,
            in_dev,
            out_dev,
            catalog,
            sample_rate=request.sample_rate,
            blocksize=request.blocksize,
            name=name,
            scenario="driver_timing_confirmation_minimal_callback",
            probes=[generate_probe(request.sample_rate, DEFAULT_TRIAL_SECONDS, seed=1901)],
        )
        results.append(payload[0])
        _write_trial_artifacts(run_dir, *payload)
        progress.complete(name)
        _save_group(run_dir, group, results, supported_rates=[request.sample_rate])
        return

    rates = _supported_rates(in_dev, out_dev, request.sample_rate)
    if group == "direct":
        for trial_number, blocksize in enumerate(_block_sizes(request), start=1):
            probes = [
                generate_probe(
                    request.sample_rate,
                    DEFAULT_TRIAL_SECONDS,
                    seed=2000 + trial_number * 10 + index,
                    pilot_hz=frequency,
                )
                for index, frequency in enumerate((997.0, 1201.0, 1601.0))
            ]
            name = f"direct_persistent_{request.sample_rate}_{blocksize}"
            payload = _direct_trial(
                request,
                in_dev,
                out_dev,
                catalog,
                sample_rate=request.sample_rate,
                blocksize=blocksize,
                name=name,
                scenario="configured_rate_persistent_three_probe_sections",
                probes=probes,
            )
            results.append(payload[0])
            _write_trial_artifacts(run_dir, *payload)
            progress.complete(name)
            for reopen_index, probe in enumerate(probes, start=1):
                name = f"direct_reopen_{request.sample_rate}_{blocksize}_{reopen_index}"
                payload = _direct_trial(
                    request,
                    in_dev,
                    out_dev,
                    catalog,
                    sample_rate=request.sample_rate,
                    blocksize=blocksize,
                    name=name,
                    scenario=f"configured_rate_complete_close_reopen_{reopen_index}",
                    probes=[probe],
                )
                results.append(payload[0])
                _write_trial_artifacts(run_dir, *payload)
                progress.complete(name)
        _save_group(run_dir, group, results, supported_rates=rates)
        return

    if group != "transitions":
        raise ValueError(f"Unknown diagnostic group: {group}")

    alternatives = [rate for rate in rates if rate != request.sample_rate]
    source_rate: int | None = None
    if alternatives:
        source_rate = 96000 if 96000 in alternatives else alternatives[-1]
        for index, release_seconds in enumerate((0.25, 1.0, 3.0), start=1):
            source_probe = generate_probe(
                source_rate, TRANSITION_TRIAL_SECONDS, seed=3000 + index
            )
            target_probe = generate_probe(
                request.sample_rate, TRANSITION_TRIAL_SECONDS, seed=4000 + index
            )
            reopen_probe = generate_probe(
                request.sample_rate, TRANSITION_TRIAL_SECONDS, seed=5000 + index
            )
            source_name = f"transition_source_{source_rate}_run_{index}"
            source = _direct_trial(
                request,
                in_dev,
                out_dev,
                catalog,
                sample_rate=source_rate,
                blocksize=0,
                name=source_name,
                scenario=f"prepare_alternate_rate_before_{release_seconds:g}s_release",
                probes=[source_probe],
                defer_analysis=True,
            )
            time.sleep(release_seconds)

            target_name = f"transition_target_{request.sample_rate}_after_{release_seconds:g}s"
            target = _direct_trial(
                request,
                in_dev,
                out_dev,
                catalog,
                sample_rate=request.sample_rate,
                blocksize=request.blocksize,
                name=target_name,
                scenario=f"first_configured_rate_start_after_{release_seconds:g}s_release",
                probes=[target_probe],
                defer_analysis=True,
            )

            reopen_name = f"transition_target_reopen_{request.sample_rate}_after_{release_seconds:g}s"
            reopen = _direct_trial(
                request,
                in_dev,
                out_dev,
                catalog,
                sample_rate=request.sample_rate,
                blocksize=request.blocksize,
                name=reopen_name,
                scenario="immediate_same_rate_reopen_after_transition",
                probes=[reopen_probe],
                defer_analysis=True,
            )

            source = _finish_direct_analysis(source)
            target = _finish_direct_analysis(target)
            reopen = _finish_direct_analysis(reopen)
            target[0].analysis["requested_driver_release_seconds"] = release_seconds
            target[0].analysis["actual_driver_release_wait_seconds"] = (
                target[3].stream_open_request_ns - source[3].stream_closed_ns
            ) / 1e9
            if target[3].monotonic_ns and reopen[3].monotonic_ns:
                reopen[0].analysis["actual_interstream_gap_seconds"] = (
                    reopen[3].monotonic_ns[0] - target[3].monotonic_ns[-1]
                ) / 1e9
            reopen[0].analysis["same_rate_reopen_request_delay_seconds"] = (
                reopen[3].stream_open_request_ns - target[3].stream_closed_ns
            ) / 1e9
            if source[3].monotonic_ns and target[3].monotonic_ns:
                target[0].analysis["actual_interstream_gap_seconds"] = (
                    target[3].monotonic_ns[0] - source[3].monotonic_ns[-1]
                ) / 1e9

            for payload in (source, target, reopen):
                results.append(payload[0])
                _write_trial_artifacts(run_dir, *payload)
                progress.complete(payload[0].name)
    else:
        for _ in range(9):
            progress.complete("rate-transition test skipped (no alternate supported rate)")
    _save_group(run_dir, group, results, supported_rates=rates, source_rate=source_rate)


def _default_launcher_command() -> list[str]:
    return [sys.executable, "-m", "harmonic_drive_qt.main"]


def run_diagnostic(request_path: str | Path, launcher_command: list[str] | None = None) -> Path:
    """Orchestrate fresh-process groups and return the ZIP intended for support."""

    request = read_request(request_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = Path(request.output_root) / f"NFS_Audio_Diagnostic_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    results: list[TrialResult] = []
    setup_problem: str | None = None
    problem_kind: str | None = None
    status = "complete"
    base_command = list(launcher_command or _default_launcher_command())
    group_sizes = {
        "backend": 4,
        "direct_smoke": 1,
        "direct": len(_block_sizes(request)) * 4,
        "transitions": 9,
    }
    total = sum(group_sizes.values())
    offset = 0

    try:
        pending_groups = ["backend"]
        while pending_groups:
            group = pending_groups.pop(0)
            command = base_command + [
                "--audio-diagnostic-group", group,
                "--audio-diagnostic-request", str(Path(request_path).resolve()),
                "--audio-diagnostic-run-dir", str(run_dir.resolve()),
                "--audio-diagnostic-progress-offset", str(offset),
                "--audio-diagnostic-progress-total", str(total),
            ]
            completed = subprocess.run(command, check=False)
            group_path = run_dir / f"group_{group}.json"
            if completed.returncode != 0 or not group_path.exists():
                raise RuntimeError(f"Fresh-process group '{group}' failed with exit code {completed.returncode}")
            group_payload = json.loads(group_path.read_text(encoding="utf-8"))
            results.extend(TrialResult(**item) for item in group_payload.get("results", []))
            offset += group_sizes[group]
            if group == "backend":
                raw_problem = group_payload.get("setup_problem")
                problem_kind = group_payload.get("problem_kind")
                setup_problem = None if raw_problem is None else str(raw_problem)
                if problem_kind == "driver_timing_failure":
                    status = "driver_timing_failed"
                    pending_groups.append("direct_smoke")
                elif setup_problem:
                    status = "setup_failed"
                else:
                    pending_groups.extend(("direct", "transitions"))
    except Exception:
        status = "fatal_error"
        (run_dir / "FATAL_ERROR.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print("Audio diagnostic failed; the error has been saved in the result bundle.", flush=True)

    driver_timing_comparison = None
    if problem_kind == "driver_timing_failure":
        backend_result = next((item for item in results if item.engine == "production_backend"), None)
        direct_result = next((item for item in results if item.name.startswith("direct_smoke_")), None)

        def timing_summary(item: TrialResult | None) -> dict[str, Any] | None:
            if item is None:
                return None
            timing = item.analysis.get("callback_timing", {})
            return {
                "effective_callback_frame_rate": timing.get("effective_callback_frame_rate"),
                "effective_to_requested_rate_ratio": timing.get("effective_to_requested_rate_ratio"),
            }

        direct_timing = timing_summary(direct_result)
        direct_ratio = (
            None
            if direct_timing is None
            else direct_timing.get("effective_to_requested_rate_ratio")
        )
        reproduced = bool(
            direct_ratio is not None and not 0.67 <= float(direct_ratio) <= 1.5
        )
        driver_timing_comparison = {
            "production_backend": timing_summary(backend_result),
            "minimal_direct_stream": direct_timing,
            "conclusion": (
                "minimal_direct_stream_unavailable"
                if direct_ratio is None
                else (
                    "runaway_timing_reproduced_in_minimal_direct_stream"
                    if reproduced
                    else "runaway_timing_not_reproduced_in_minimal_direct_stream"
                )
            ),
        }

    passed = sum(bool(item.analysis.get("passed")) and not item.error for item in results)
    summary = {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "completed": datetime.now().astimezone().isoformat(),
        "status": status,
        "setup_problem": setup_problem,
        "problem_kind": problem_kind,
        "driver_timing_comparison": driver_timing_comparison,
        "trial_count": len(results),
        "passed_trials": passed,
        "failed_trials": len(results) - passed,
        "results": [asdict(item) for item in results],
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    (run_dir / "README.txt").write_text(
        "NFS electrical-loopback audio diagnostic\n\n"
        "Send the adjacent *_SEND_THIS_FILE.zip archive for analysis.\n"
        "The production backend, direct configured-rate tests, and sample-rate transitions ran in "
        "separate fresh processes. A failed automatic classification is evidence to inspect, not "
        "by itself proof of a driver fault.\n",
        encoding="utf-8",
    )
    archive = _zip_directory(run_dir)
    print(f"Audio diagnostic: 100% - complete\nSend this file: {archive}", flush=True)
    return archive


def aggregate_existing_bundle(path: str | Path) -> dict[str, Any]:
    """Load the machine-readable summary from a returned folder or ZIP."""

    source = Path(path)
    if source.is_dir():
        return json.loads((source / "summary.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(source) as bundle:
        return json.loads(bundle.read("summary.json").decode("utf-8"))
