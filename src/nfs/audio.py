"""
Audio Measurement Engine
========================

This module implements a robust acoustic measurement engine using Exponential
Sine Sweeps (ESS). It is designed for high-precision Impulse Response (IR)
acquisition with driver protection and distortion analysis capabilities.

Reading guide: one measurement, in order
---------------------------------------
1. AudioFactory assembles generators, alignment, deconvolution and optional
   driver protection. Audio coordinates them; it does not implement their DSP.
2. Audio._prepare_sweep builds the clean digital ESS and its extended inverse.
   It adds the loopback marker, settling gap, simultaneous sweeps and silence.
   Speaker protection and test harmonics affect the speaker output only.
3. Audio._run_sweep records both inputs together. The real-time callback only
   moves samples and queues metering; no FFTs or file writes belong there.
4. Audio._finish_sweep uses the electrical marker to align BOTH channels by
   the same amount, averages repeats and keeps the recorded decay. This removes
   common interface latency without removing the loudspeaker's acoustic delay.
5. DeconvolutionEngine.process_ir convolves the microphone with the clean
   digital inverse, optionally compensating the driver protection filter.
   The result contains the linear IR and earlier, separated harmonic responses.
6. If interface correction is enabled, _build_loopback_kernel deconvolves the
   loopback AND digital reference, isolates their linear responses and designs
   a gain-limited correction. Loopback harmonics never enter that inverse.
   Only the deliberately retained bandwidth roll-off gets minimum-phase shape;
   the measured loudspeaker/room response is not converted to minimum phase.
7. The complete correction inverse is convolved with the microphone IR. Both
   inverses retain every calculated sample; padding prevents circular wraparound.
8. _export_records removes processing offsets, preserves the marker-derived
   origin, selects the export duration and fades the full record's far end.
   The linear file excludes earlier harmonics; the full file retains them.
9. Audio.measure_ir verifies the result and optionally saves WAVs and debug
   artifacts. MockInterfaceAudio replaces acquisition but reuses this pipeline.

Conventions worth keeping in mind
--------------------------------
* fs means sample rate in Hz. Sample indices are integers, starting at zero.
* A time origin is a timing reference, not necessarily the largest IR sample.
* FFT arrays are periodic representations. Extra zeros provide space for linear
  convolution; padding does not create new measurement information.
* A window on the measured loopback selects its linear IR. It is NOT a window
  on the microphone's room decay or a truncation of either inverse filter.
* Playback levels are peak dBFS; exported IR amplitudes use the sine-RMS
  convention. There is no automatic output high-pass filter.

Key Features:
    * Weighted Farina Method: Uses Exponential Sine Sweeps (ESS) to separate 
      linear impulse response from harmonic distortion products. Distortion 
      orders appear at negative time offsets relative to the main impulse.
    * Matched ESS Deconvolution: A finite regularized inverse of the faded
      exponential sweep reduces endpoint residue while retaining Farina
      harmonic separation. Silent guards preserve the existing IR timing.
    * Linear Loopback Correction: Isolates the electrical reference's linear
      IR before regularized inversion, excluding its harmonic responses.
    * Driver Protection: Configurable High-Pass Filter (Minimum Phase) applied 
      to the playback signal to protect tweeters/drivers from LF damage, with 
      optional magnitude-capped inverse correction filter during deconvolution.
    * Harmonic Injection: Debug feature to inject artificial H2/H3 into the 
      sweep to verify distortion analysis logic.
    * Robust Alignment: Uses a Barker-13 code for precise temporal alignment. 
      Supports multi-sweep averaging with sweep alignment either by the first 
      marker and expected sample index of subsequent sweeps, or per-sweep 
      alignment for robustness against driver glitches.
    * IR Separation: Separates the Linear IR from the full capture (containing 
      distortion) and saves both as separate files.
    * DSP Verification Tool: Automated verification of Signal-to-Noise Ratio (SNR),
      Total Harmonic Distortion (THD), and Peak-to-Sidelobe Ratio (PSR) for alignment quality.
    * Mock Interface: A digital twin loopback mode that simulates hardware latency 
      and filter effects, allowing testing without physical audio devices.
"""

import configparser
import ctypes
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from nfs import registry

import numpy as np
import scipy.signal
import scipy.fft
import soundfile as sf

from loguru import logger

from .datatypes import CylindricalPosition
from .utils.dsp import DSPUtils

from . import _audio_backend as sd


@contextmanager
def _asio_thread_context(enabled: bool):
    """Keep Windows COM initialized on the calling thread until ASIO closes."""
    # COM is thread-specific. The thread that opens an ASIO stream must set it
    # up first, then keep it available until the stream has been closed.
    if not enabled or sys.platform != "win32":
        yield
        return
    ole32 = ctypes.windll.ole32
    # COINIT_APARTMENTTHREADED = 2. Mask to unsigned so Windows error codes
    # compare the same way whether ctypes returns a signed or unsigned value.
    result = ole32.CoInitializeEx(None, 2) & 0xFFFFFFFF
    # 0: new apartment; 1: already initialized here; RPC_E_CHANGED_MODE:
    # another apartment model is active, and we do not own a COM reference.
    if result not in (0, 1, 0x80010106):
        raise RuntimeError(f"ASIO COM initialization failed (HRESULT 0x{result:08X})")
    try:
        yield
    finally:
        # Balance our own successful initialization, even if the stream fails.
        if result in (0, 1):
            ole32.CoUninitialize()


def _stream_channel_settings(api_name: str, physical_channels: Tuple[int, int],
                             wasapi_exclusive: bool) -> Tuple[int, Any]:
    """Map two logical roles to physical channels for either capture or playback.

    ASIO presents only the selected channels, in the supplied role order.
    Other APIs expose columns up to the highest physical channel number;
    callbacks must therefore address those physical columns explicitly.
    """
    if "ASIO" in api_name:
        return 2, sd.AsioSettings(channel_selectors=list(physical_channels))
    channel_count = max(physical_channels) + 1  # Indices start at zero; counts do not.
    settings = sd.WasapiSettings(exclusive=wasapi_exclusive) if "WASAPI" in api_name else None
    return channel_count, settings


_METER_EPS = 1e-12
_METER_STALE_TIMEOUT_S = 1.5
SINE_RMS_FROM_PEAK = 1.0 / np.sqrt(2.0)
# Keep the nominal origin at this zero-based index in every linear export.
# The actual peak can occur later: speaker delay must not be aligned away.
_LINEAR_IR_ORIGIN_SAMPLE = 50
_EXPORT_FADE_FRACTION = 0.1  # End fade lasts 10% of the playback sweep duration.
_METER_LOCK = threading.Lock()
_METER_STATE = {
    "active": False,
    "updated_at": 0.0,
    "outputs": [
        {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "clip": False},
        {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "clip": False},
    ],
    "inputs": [
        {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "clip": False},
        {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "clip": False},
    ],
    "a_weighted_inputs": [
        {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "clip": False},
        {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "clip": False},
    ],
    "c_weighted_inputs": [
        {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "clip": False},
        {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "clip": False},
    ],
}
_A_WEIGHTING_FILTERS: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
_C_WEIGHTING_FILTERS: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}


def _channel_meter(samples: np.ndarray) -> Dict[str, Any]:
    if samples.size == 0:
        return {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "clip": False}
    abs_samples = np.abs(samples.astype(np.float64, copy=False))
    peak = float(np.max(abs_samples))
    rms = float(np.sqrt(np.mean(np.square(abs_samples))))
    return {
        "rms_dbfs": max(-120.0, 20.0 * np.log10(rms + _METER_EPS)),
        "peak_dbfs": max(-120.0, 20.0 * np.log10(peak + _METER_EPS)),
        "clip": peak >= 0.999,
    }


def _two_channel_meters(frames: np.ndarray) -> List[Dict[str, Any]]:
    if frames.ndim == 1:
        frames = frames[:, None]
    meters = []
    for channel_index in range(2):
        if channel_index < frames.shape[1]:
            meters.append(_channel_meter(frames[:, channel_index]))
        else:
            meters.append(_channel_meter(np.array([], dtype=np.float32)))
    return meters


def _a_weighting_filter(sample_rate: int) -> Tuple[np.ndarray, np.ndarray]:
    cached = _A_WEIGHTING_FILTERS.get(sample_rate)
    if cached is not None:
        return cached

    f1 = 20.598997
    f2 = 107.65265
    f3 = 737.86223
    f4 = 12194.217
    a1000 = 1.9997
    nums = [
        (2 * np.pi * f4) ** 2 * (10 ** (a1000 / 20)),
        0.0,
        0.0,
        0.0,
        0.0,
    ]
    dens = np.polymul(
        [1.0, 4 * np.pi * f4, (2 * np.pi * f4) ** 2],
        [1.0, 4 * np.pi * f1, (2 * np.pi * f1) ** 2],
    )
    dens = np.polymul(np.polymul(dens, [1.0, 2 * np.pi * f3]), [1.0, 2 * np.pi * f2])
    filt = scipy.signal.bilinear(nums, dens, sample_rate)
    _A_WEIGHTING_FILTERS[sample_rate] = filt
    return filt


def _c_weighting_filter(sample_rate: int) -> Tuple[np.ndarray, np.ndarray]:
    cached = _C_WEIGHTING_FILTERS.get(sample_rate)
    if cached is not None:
        return cached

    f1 = 20.598997
    f4 = 12194.217
    c1000 = 0.0619
    nums = [
        (2 * np.pi * f4) ** 2 * (10 ** (c1000 / 20)),
        0.0,
        0.0,
    ]
    dens = np.polymul(
        [1.0, 4 * np.pi * f4, (2 * np.pi * f4) ** 2],
        [1.0, 4 * np.pi * f1, (2 * np.pi * f1) ** 2],
    )
    filt = scipy.signal.bilinear(nums, dens, sample_rate)
    _C_WEIGHTING_FILTERS[sample_rate] = filt
    return filt


def _two_channel_weighted_meters(
    frames: np.ndarray,
    sample_rate: int,
    filter_factory,
) -> List[Dict[str, Any]]:
    b, a = filter_factory(int(sample_rate))
    if frames.ndim == 1:
        frames = frames[:, None]
    weighted = np.zeros((frames.shape[0], 2), dtype=np.float32)
    for channel_index in range(2):
        if channel_index < frames.shape[1]:
            weighted[:, channel_index] = scipy.signal.lfilter(
                b,
                a,
                frames[:, channel_index],
            ).astype(np.float32)
    return _two_channel_meters(weighted)


def _two_channel_a_weighted_meters(frames: np.ndarray, sample_rate: int) -> List[Dict[str, Any]]:
    return _two_channel_weighted_meters(frames, sample_rate, _a_weighting_filter)


def _two_channel_c_weighted_meters(frames: np.ndarray, sample_rate: int) -> List[Dict[str, Any]]:
    return _two_channel_weighted_meters(frames, sample_rate, _c_weighting_filter)


def _role_frames(
    frames: np.ndarray,
    first_index: int,
    second_index: int,
) -> np.ndarray:
    if frames.ndim == 1:
        frames = frames[:, None]
    role_data = np.zeros((frames.shape[0], 2), dtype=np.float32)
    if 0 <= first_index < frames.shape[1]:
        role_data[:, 0] = frames[:, first_index]
    if 0 <= second_index < frames.shape[1]:
        role_data[:, 1] = frames[:, second_index]
    return role_data


class _AudioMeterWorker:
    """Moves metering and callback-status reporting off the audio callback."""

    _STATUS_NAMES = (
        "input_underflow",
        "input_overflow",
        "output_underflow",
        "output_overflow",
    )

    def __init__(self, sample_rate: int, blocksize: int = 0) -> None:
        self.sample_rate = int(sample_rate)
        # Capacity absorbs brief scheduling stalls; it is not a delay target.
        # Processing is capped to the newest 100 ms so meters remain responsive.
        self._capacity = max(8192, self.sample_rate, int(blocksize))
        self._max_meter_frames = max(1, int(round(self.sample_rate * 0.1)))
        self._outputs = np.zeros((self._capacity, 2), dtype=np.float32)
        self._inputs = np.zeros((self._capacity, 2), dtype=np.float32)
        self._write_index = 0
        self._read_index = 0
        self._dropped_meter_blocks = 0
        self._skipped_meter_frames = 0
        self._status_counts = {name: 0 for name in self._STATUS_NAMES}
        self._status_counts["other"] = 0
        self._last_reported_counts = dict(self._status_counts)
        self._last_reported_drops = 0
        self._last_reported_skipped_frames = 0
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="audio-meter-worker",
        )
        a_b, a_a = _a_weighting_filter(self.sample_rate)
        c_b, c_a = _c_weighting_filter(self.sample_rate)
        self._a_filter = (a_b, a_a)
        self._c_filter = (c_b, c_a)
        self._a_zi = np.zeros((2, max(len(a_a), len(a_b)) - 1), dtype=np.float64)
        self._c_zi = np.zeros((2, max(len(c_a), len(c_b)) - 1), dtype=np.float64)

    def start(self) -> None:
        self._thread.start()

    def submit(
        self,
        outputs: np.ndarray,
        output_roles: Tuple[int, int],
        inputs: np.ndarray,
        input_roles: Tuple[int, int],
        status,
    ) -> None:
        """Copy one callback block into the preallocated meter ring."""
        self._record_status(status)
        frames = min(len(outputs), len(inputs))
        if frames <= 0:
            return
        available = self._capacity - (self._write_index - self._read_index)
        if frames > available:
            self._dropped_meter_blocks += 1
            return

        start = self._write_index % self._capacity
        first_count = min(frames, self._capacity - start)
        self._copy_roles(self._outputs, start, outputs, output_roles, first_count, 0)
        self._copy_roles(self._inputs, start, inputs, input_roles, first_count, 0)
        remaining = frames - first_count
        if remaining:
            self._copy_roles(self._outputs, 0, outputs, output_roles, remaining, first_count)
            self._copy_roles(self._inputs, 0, inputs, input_roles, remaining, first_count)
        # Publish only after both channel pairs have been copied completely.
        self._write_index += frames

    @staticmethod
    def _copy_roles(
        destination: np.ndarray,
        destination_start: int,
        source: np.ndarray,
        roles: Tuple[int, int],
        count: int,
        source_start: int,
    ) -> None:
        destination_end = destination_start + count
        source_end = source_start + count
        for destination_channel, source_channel in enumerate(roles):
            if 0 <= source_channel < source.shape[1]:
                destination[destination_start:destination_end, destination_channel] = source[
                    source_start:source_end,
                    source_channel,
                ]
            else:
                destination[destination_start:destination_end, destination_channel] = 0.0

    def _record_status(self, status) -> None:
        if not status:
            return
        matched = False
        for name in self._STATUS_NAMES:
            if bool(getattr(status, name, False)):
                self._status_counts[name] += 1
                matched = True
        # Priming is a normal stream-start condition, not an xrun.
        if bool(getattr(status, "priming_output", False)):
            matched = True
        if not matched:
            self._status_counts["other"] += 1

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        if self._thread.is_alive():
            logger.warning("Audio meter worker did not stop within 2 seconds")
        self._report_callback_status(force=True)

    def _run(self) -> None:
        next_status_report = time.monotonic() + 1.0
        while not self._stop_event.is_set():
            write_index = self._write_index
            if self._read_index >= write_index:
                self._stop_event.wait(0.02)
            else:
                self._process_available(write_index)
            now = time.monotonic()
            if now >= next_status_report:
                self._report_callback_status()
                next_status_report = now + 1.0

    def _process_available(self, write_index: int) -> None:
        available = write_index - self._read_index
        if available > self._max_meter_frames:
            skipped = available - self._max_meter_frames
            self._read_index += skipped
            self._skipped_meter_frames += skipped
            # The weighting filters cannot preserve continuity across skipped
            # display-only samples, so restart their state at the newest window.
            self._a_zi.fill(0.0)
            self._c_zi.fill(0.0)
        frames = min(write_index - self._read_index, self._max_meter_frames)
        start = self._read_index % self._capacity
        first_count = min(frames, self._capacity - start)
        if first_count == frames:
            outputs = self._outputs[start:start + frames].copy()
            inputs = self._inputs[start:start + frames].copy()
        else:
            remaining = frames - first_count
            outputs = np.concatenate((self._outputs[start:], self._outputs[:remaining]))
            inputs = np.concatenate((self._inputs[start:], self._inputs[:remaining]))
        self._read_index += frames

        a_weighted = self._apply_weighting(inputs, self._a_filter, self._a_zi)
        c_weighted = self._apply_weighting(inputs, self._c_filter, self._c_zi)
        update_audio_meter_state(
            outputs,
            inputs,
            active=True,
            a_weighted_inputs=a_weighted,
            c_weighted_inputs=c_weighted,
        )

    @staticmethod
    def _apply_weighting(
        inputs: np.ndarray,
        coefficients: Tuple[np.ndarray, np.ndarray],
        zi: np.ndarray,
    ) -> np.ndarray:
        b, a = coefficients
        weighted = np.zeros_like(inputs, dtype=np.float32)
        for channel_index in range(min(2, inputs.shape[1])):
            filtered, zi[channel_index] = scipy.signal.lfilter(
                b,
                a,
                inputs[:, channel_index],
                zi=zi[channel_index],
            )
            weighted[:, channel_index] = filtered.astype(np.float32)
        return weighted

    def _report_callback_status(self, force: bool = False) -> None:
        changes = {
            name: count - self._last_reported_counts[name]
            for name, count in self._status_counts.items()
            if count != self._last_reported_counts[name]
        }
        dropped = self._dropped_meter_blocks - self._last_reported_drops
        skipped = self._skipped_meter_frames - self._last_reported_skipped_frames
        if changes:
            logger.warning("Audio callback status counts: {}", changes)
            self._last_reported_counts = dict(self._status_counts)
        if dropped:
            logger.warning(
                "Audio meter worker dropped {} display block(s); audio capture was unaffected",
                dropped,
            )
            self._last_reported_drops = self._dropped_meter_blocks
        elif force:
            self._last_reported_drops = self._dropped_meter_blocks
        if skipped:
            logger.warning(
                "Audio meter worker skipped {} stale display sample frame(s) to remain current",
                skipped,
            )
            self._last_reported_skipped_frames = self._skipped_meter_frames
        elif force:
            self._last_reported_skipped_frames = self._skipped_meter_frames


def update_audio_meter_state(
    outputs: Optional[np.ndarray] = None,
    inputs: Optional[np.ndarray] = None,
    *,
    active: bool = True,
    sample_rate: Optional[int] = None,
    a_weighted_inputs: Optional[np.ndarray] = None,
    c_weighted_inputs: Optional[np.ndarray] = None,
) -> None:
    with _METER_LOCK:
        _METER_STATE["active"] = active
        _METER_STATE["updated_at"] = time.time()
        if outputs is not None:
            _METER_STATE["outputs"] = _two_channel_meters(outputs)
        if inputs is not None:
            _METER_STATE["inputs"] = _two_channel_meters(inputs)
            if a_weighted_inputs is not None:
                _METER_STATE["a_weighted_inputs"] = _two_channel_meters(a_weighted_inputs)
            elif sample_rate is not None:
                _METER_STATE["a_weighted_inputs"] = _two_channel_a_weighted_meters(
                    inputs,
                    int(sample_rate),
                )
            if c_weighted_inputs is not None:
                _METER_STATE["c_weighted_inputs"] = _two_channel_meters(c_weighted_inputs)
            elif sample_rate is not None:
                _METER_STATE["c_weighted_inputs"] = _two_channel_c_weighted_meters(
                    inputs,
                    int(sample_rate),
                )


def reset_audio_meter_state(active: bool = False) -> None:
    update_audio_meter_state(
        np.zeros((1, 2), dtype=np.float32),
        np.zeros((1, 2), dtype=np.float32),
        active=active,
    )


def get_audio_meter_state() -> Dict[str, Any]:
    with _METER_LOCK:
        if (
            _METER_STATE["active"]
            and _METER_STATE["updated_at"]
            and time.time() - float(_METER_STATE["updated_at"]) > _METER_STALE_TIMEOUT_S
        ):
            _METER_STATE["active"] = False
            _METER_STATE["outputs"] = _two_channel_meters(np.zeros((1, 2), dtype=np.float32))
            _METER_STATE["inputs"] = _two_channel_meters(np.zeros((1, 2), dtype=np.float32))
            _METER_STATE["a_weighted_inputs"] = _two_channel_meters(np.zeros((1, 2), dtype=np.float32))
            _METER_STATE["c_weighted_inputs"] = _two_channel_meters(np.zeros((1, 2), dtype=np.float32))
        return {
            "active": bool(_METER_STATE["active"]),
            "updated_at": float(_METER_STATE["updated_at"]),
            "outputs": [dict(item) for item in _METER_STATE["outputs"]],
            "inputs": [dict(item) for item in _METER_STATE["inputs"]],
            "a_weighted_inputs": [dict(item) for item in _METER_STATE["a_weighted_inputs"]],
            "c_weighted_inputs": [dict(item) for item in _METER_STATE["c_weighted_inputs"]],
        }


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITIES
# ─────────────────────────────────────────────────────────────────────────────


def clean_device_name(name: str) -> str:
    """Removes messy Windows driver paths and registry strings from device names."""
    if "@System32" in name or ".sys" in name:
        # Splits by semicolon and takes the last part, usually the readable name
        parts = name.split(";")
        clean = parts[-1].replace("%0", "").replace("%1", "").strip()
        return clean if clean else "Bluetooth Audio Device"
    return name


def get_devices_and_channels() -> dict:
    """
    Queries audio devices and returns a dict indexed by Device ID.
    Channel indices are 0-based because they are used directly as sounddevice
    stream buffer indices.
    """
    # Device lookup must be read-only: PortAudio is shared by every stream.
    # Restarting it here can invalidate an active driver, or reload ASIO on
    # a temporary UI worker that exits before the next capture. Restart the
    # application after hot-plugging hardware if the catalog has not updated.
    try:
        apis = sd.query_hostapis()
        devs = sd.query_devices()
    except Exception as exc:
        logger.warning("Unable to discover audio devices: {}", exc)
        return {}

    device_catalog = {}

    for api_idx, a in enumerate(apis):
        api_name = a['name']

        for dev_idx, d in enumerate(devs):
            if d['hostapi'] == api_idx:
                max_in = d['max_input_channels']
                max_out = d['max_output_channels']

                in_ch_indices = list(range(max_in)) if max_in > 0 else []
                out_ch_indices = list(range(max_out)) if max_out > 0 else []

                display_name = clean_device_name(d['name'])

                device_catalog[dev_idx] = {
                    'name': display_name,
                    'hostapi': api_name,
                    'input_channels': in_ch_indices,
                    'output_channels': out_ch_indices
                }

    return device_catalog


def get_supported_sample_rates(input_device_id: Optional[int], output_device_id: Optional[int]) -> List[int]:
    """
    Return common sample rates supported by the selected input/output devices.

    PortAudio does not expose a guaranteed exhaustive list for every host API, so
    probe standard audio rates and keep those accepted by all selected devices.
    """
    try:
        check_input_settings = sd.check_input_settings
        check_output_settings = sd.check_output_settings
    except Exception as exc:
        logger.warning("Unable to discover supported audio sample rates: {}", exc)
        return []

    common_rates = [44100, 48000, 88200, 96000, 176400, 192000]
    supported = []

    for rate in common_rates:
        try:
            if input_device_id is not None:
                check_input_settings(device=input_device_id, samplerate=rate)
            if output_device_id is not None:
                check_output_settings(device=output_device_id, samplerate=rate)
        except Exception:
            continue
        supported.append(rate)

    return supported


def find_device_id_by_name(
    device_name: str,
    hostapi: Optional[str] = None,
    *,
    require_input: bool = False,
    require_output: bool = False,
) -> Optional[int]:
    """
    Find the current PortAudio device ID for a previously saved device name.

    Device IDs can change when hardware is connected or removed. Saving the
    cleaned display name and host API lets us resolve the current ID at startup.
    """
    target_name = (device_name or "").strip().casefold()
    target_api = (hostapi or "").strip().casefold()
    if not target_name:
        return None

    catalog = get_devices_and_channels()
    fallback_match = None
    for dev_id, info in catalog.items():
        if info.get("name", "").strip().casefold() != target_name:
            continue
        if require_input and not info.get("input_channels"):
            continue
        if require_output and not info.get("output_channels"):
            continue
        if target_api and info.get("hostapi", "").strip().casefold() == target_api:
            return dev_id
        if fallback_match is None:
            fallback_match = dev_id

    if target_api:
        return None

    return fallback_match


def resolve_device_id_by_saved_identity(
    device_name: str,
    hostapi: str,
    *,
    role_label: str,
    require_input: bool = False,
    require_output: bool = False,
) -> int:
    """Resolve the current PortAudio ID for the saved user-facing selection."""
    target_name = (device_name or "").strip()
    target_api = (hostapi or "").strip()
    if not target_name or not target_api:
        raise ValueError(
            f"Audio {role_label} device selection is incomplete. "
            "Open Audio Setup and select the audio API and device again."
        )

    name_key = target_name.casefold()
    api_key = target_api.casefold()
    catalog = get_devices_and_channels()
    name_matches = []
    api_matches = []
    for dev_id, info in catalog.items():
        if require_input and not info.get("input_channels"):
            continue
        if require_output and not info.get("output_channels"):
            continue
        info_name = str(info.get("name", "")).strip()
        info_api = str(info.get("hostapi", "")).strip()
        if info_name.casefold() == name_key:
            name_matches.append(info_api)
        if info_api.casefold() == api_key:
            api_matches.append(info_name)
        if info_name.casefold() == name_key and info_api.casefold() == api_key:
            return int(dev_id)

    if name_matches:
        available = ", ".join(sorted(set(name_matches)))
        raise ValueError(
            f"No {role_label} audio device matches saved selection "
            f"'{target_name}' on '{target_api}'. The device name exists under: {available}. "
            "Open Audio Setup and select the intended API/device pair."
        )
    if api_matches:
        available = ", ".join(sorted(set(api_matches))[:5])
        raise ValueError(
            f"No {role_label} audio device matches saved selection "
            f"'{target_name}' on '{target_api}'. The host API exists, but with different devices "
            f"such as: {available}. Open Audio Setup and select the intended API/device pair."
        )
    raise ValueError(
        f"No {role_label} audio device matches saved selection "
        f"'{target_name}' on '{target_api}'. Open Audio Setup and select the audio API and device again."
    )


class DSPVerificationTool:
    """
    Calculates Quality Metrics from Impulse Responses and Alignment data.
    Provides automated verification of SNR, THD, and Timing Jitter.
    """

    def __init__(self, fs: int) -> None:
        """
        Initialize the DSP verification tool.

        :param fs: The sample rate (Hz).
        """
        self.fs = fs

    def calculate_metrics(self, h_full: np.ndarray, h_linear: np.ndarray, psr: float,
                          psr_reference: Optional[float] = None, *,
                          linear_start_sample: Optional[int] = None) -> Dict[str, float]:
        """
        Calculates a suite of DSP quality metrics.
        
        :param h_full: Full IR including distortion products at negative time.
        :param h_linear: Cropped linear IR.
        :param psr: Peak-to-Sidelobe Ratio from the alignment engine.
        :param linear_start_sample: Known crop start in h_full, needed when
            long room-decay exports move the linear arrival away from its midpoint.
        :return: Dictionary of metrics.
        """
        metrics = {}

        # 1. SNR Estimation (Peak to Noise Floor)
        # We estimate noise from the very end of the h_full record where signal should have decayed.
        # This gives a practical indication of measurement dynamic range.
        noise_floor_window = int(0.05 * self.fs)  # 50ms window
        if len(h_full) > noise_floor_window:
            noise_floor = np.std(h_full[-noise_floor_window:])
            peak_val = np.max(np.abs(h_linear))
            metrics['snr_db'] = float(20 * np.log10(peak_val / (noise_floor + 1e-12)))
        else:
            metrics['snr_db'] = 0.0

        # 2. THD Estimation (Distortion to Linear Energy)
        # In Farina's method, distortion products appear BEFORE the linear peak.
        # Use the known linear crop boundary; only legacy callers need the midpoint estimate.
        # This is a broad energy diagnostic, not a calibrated per-frequency THD curve.
        split_idx = len(h_full) // 2  # Approximate based on DeconvolutionEngine logic
        distortion_end = split_idx - (_LINEAR_IR_ORIGIN_SAMPLE + 1) if linear_start_sample is None else linear_start_sample
        distortion_part = h_full[:distortion_end]

        linear_energy = np.sum(h_linear ** 2)
        distortion_energy = np.sum(distortion_part ** 2)

        metrics['thd_pct'] = float(np.sqrt(distortion_energy / (linear_energy + 1e-12)) * 100.0)

        # 3. Alignment Quality
        metrics['psr'] = float(psr)
        metrics['psr_db'] = float(20.0 * np.log10(psr)) if psr > 0 else 0.0
        if psr_reference is not None and psr_reference > 1.0:
            reference_db = float(20.0 * np.log10(psr_reference))
            metrics['psr_reference_db'] = reference_db
            # This is a relative clarity score, not a probability that the
            # alignment is correct. The raw PSR remains in the saved metrics.
            metrics['alignment_score_10'] = round(float(np.clip(
                10.0 * metrics['psr_db'] / reference_db, 0.0, 10.0
            )), 1)

        # 4. Crest Factor of IR (Indicates impulsive vs smeared)
        # Ratio of peak amplitude to RMS value. A high crest factor means a sharp, clean impulse.
        metrics['crest_factor'] = float(np.max(np.abs(h_linear)) / (np.sqrt(np.mean(h_linear ** 2)) + 1e-12))

        return metrics

    def verify(self, metrics: Dict[str, float]) -> List[str]:
        """Checks metrics against safety thresholds and returns a list of warnings."""
        warnings = []
        if metrics.get('snr_db', 100) < 30:
            warnings.append(f"LOW SNR: {metrics['snr_db']:.1f} dB. Measurement may be noisy.")
        if metrics.get('thd_pct', 0) > 10:
            warnings.append(f"HIGH DISTORTION: {metrics['thd_pct']:.2f}%. Check for clipping or transducer stress.")
        if metrics.get('psr', 10) < 3.0:
            if 'alignment_score_10' in metrics:
                warnings.append(
                    f"POOR ALIGNMENT: Marker clarity is {metrics['psr_db']:.1f} dB / "
                    f"{metrics['psr_reference_db']:.1f} dB reference "
                    f"({metrics['alignment_score_10']:.1f}/10)."
                )
            else:
                warnings.append(
                    f"POOR ALIGNMENT: Marker clarity is {metrics.get('psr_db', 0.0):.1f} dB."
                )

        return warnings


# ─────────────────────────────────────────────────────────────────────────────
#  SIGNAL GENERATORS & PIPELINE STEPS
# ─────────────────────────────────────────────────────────────────────────────

class MarkerGenerator:
    """
    Generates a band-limited Barker-13 sequence for precise temporal alignment.
    Barker codes have ideal autocorrelation properties (low sidelobes), making them
    superior to simple pulses for synchronization in noisy environments.
    """

    def __init__(self, fs: int, dur_ms: float, bw_hz: Tuple[float, float], level_dbfs: float) -> None:
        """
        Initialize the marker generator.

        :param fs: Sample rate (Hz).
        :param dur_ms: Duration in milliseconds.
        :param bw_hz: Bandwidth tuple (low, high) in Hz.
        :param level_dbfs: Level in dBFS.
        """
        self.fs = fs
        self.dur_ms = dur_ms
        self.bw_hz = bw_hz
        self.level_dbfs = level_dbfs

    def generate(self) -> np.ndarray:
        """Generates band-limited Barker code marker with tapered edges and normalized level"""
        chips = np.array([+1, +1, +1, +1, +1, -1, -1, +1, +1, -1, +1, -1, +1], dtype=np.float32)
        n = max(16, int(round(self.dur_ms / 1000.0 * self.fs)))

        # Interpolate chips to desired duration
        # We stretch the 13-chip Barker sequence to span the requested duration in milliseconds.
        marker_raw = np.interp(np.linspace(0, len(chips) - 1, n), np.arange(chips.size), chips)

        # Taper edges using the unified _hann_fade
        marker_raw = DSPUtils.hann_fade(marker_raw.astype(np.float32), 1.0, self.fs)

        # Frequency Domain Band-Limiting
        # Ensures the marker doesn't excite resonances outside the measurement band
        Nfft = int(2 ** np.ceil(np.log2(n * 2)))
        M = np.fft.rfft(marker_raw, n=Nfft)
        freqs = np.fft.rfftfreq(Nfft, 1 / self.fs)
        f_lo, f_hi = self.bw_hz
        mask = np.ones_like(freqs, dtype=np.float32)

        # Apply cosine ramps at band edges
        if f_lo > 0:
            idx = freqs < f_lo
            ramp = 0.5 * (1 - np.cos(np.pi * np.clip(freqs[idx] / max(1e-9, f_lo), 0, 1)))
            mask[idx] = ramp ** 2
        if f_hi < self.fs / 2:
            idx = freqs > f_hi
            ramp = 0.5 * (
                    1 - np.cos(np.pi * np.clip((self.fs / 2 - freqs[idx]) / max(1e-9, (self.fs / 2 - f_hi)), 0, 1)))
            mask[idx] = ramp ** 2

        marker_bl = np.fft.irfft(M * mask, n=Nfft)[:n].astype(np.float32)
        marker_bl *= DSPUtils.db_to_lin(self.level_dbfs) / (np.max(np.abs(marker_bl)) + 1e-12)
        return marker_bl


def _full_inverse(q: np.ndarray, negative_samples: Optional[int] = None) -> Tuple[np.ndarray, int]:
    """Arrange a periodic inverse around its origin, retaining every sample.

    The correction uses equal negative/positive support; the sweep supplies
    its explicit origin. Neither inverse is shortened or tapered here.
    """
    split = len(q) // 2 if negative_samples is None else len(q) - negative_samples
    return np.concatenate((q[split:], q[:split])), len(q) - split


class SweepGenerator:
    """
    Generates an Exponential Sine Sweep (ESS) and its CLEAN inverse filter.
    
    Outputs:
      1. The fundamental sweep signal.
      2. The phase array (useful for optional harmonic injection downstream).
      3. A regularized inverse of the clean, faded fundamental.
    """

    def __init__(self, fs: int, duration_s: float, f1: float, level_dbfs: float) -> None:
        """
        Initialize the sweep generator.

        :param fs: Sample rate (Hz).
        :param duration_s: Duration in seconds.
        :param f1: Starting frequency (Hz).
        :param level_dbfs: Level in dBFS.
        """
        self.fs = fs
        self.T = duration_s
        self.f1 = f1
        self.level_dbfs = level_dbfs

    def generate(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Generate a Farina ESS and a finite, regularized inverse of its playback.

        The inverse retains support on both sides of the reversed sweep.
        inverse_origin_sample locates its convolution peak; playback length
        and the exported IR origin are independent of that support.
        """
        if not np.isfinite(self.T) or self.T < 0.1:
            raise ValueError("Sweep duration must be finite and at least 0.1 seconds")
        if not 0 < self.f1 < self.fs / 2:
            raise ValueError("Sweep start frequency must be between zero and Nyquist")
        # A sample is one point in time. At 48 kHz, 1.5 s is 72,000 samples.
        n = int(round(self.T * self.fs))
        # Reserve silence inside this fixed-length array at both ends. These
        # guards keep endpoint artifacts away from the useful inverse response.
        guard = min(round(0.010 * self.fs), n // 20)
        active_n = n - 2 * guard
        self.guard_samples = guard
        self.active_duration_s = active_n / self.fs
        # Only the active samples advance through frequency; the guards do not.
        active_t = np.arange(active_n, dtype=np.float64) / self.fs
        # L is the time in which an exponential sweep's frequency multiplies
        # by e (~2.718). H2 advances by L*ln(2); H3 by L*ln(3).
        self.log_sweep_rate = (active_n / self.fs) / np.log((self.fs / 2) / self.f1)
        # Integrate the exponentially rising frequency to obtain phase in
        # radians. expm1(x) accurately computes exp(x)-1 near the start.
        active_phase = 2 * np.pi * self.f1 * self.log_sweep_rate * np.expm1(
            active_t / self.log_sweep_rate
        )
        # A 1 ms Hann fade eases playback in and out instead of switching
        # abruptly. The fade is part of the sweep that the inverse must match.
        active_envelope = DSPUtils.hann_fade(np.ones(active_n), 1.0, self.fs)
        self.fade_envelope = np.pad(active_envelope, (guard, guard))
        # Store the frequency at each sample for safe H2/H3 test injection.
        self.instantaneous_hz = np.pad(
            self.f1 * np.exp(active_t / self.log_sweep_rate), (guard, guard)
        )
        phase = np.pad(active_phase, (guard, guard))
        # The speaker receives this sine wave, with the silent guards attached.
        s_fund = np.sin(phase) * self.fade_envelope

        # Numerical inversion of the clean, faded ESS retains Farina harmonic
        # separation. Never include injected harmonics or the protection HPF
        # in this reference. Regularization limits gain where excitation is weak.
        # Leave at least a sweep's worth of additional inverse support. Center
        # the reversed sweep within it so both endpoint tails fit, rather than
        # placing negative-time support at the far end of the periodic IFFT.
        n_fft = 1 << (2 * n - 2).bit_length()
        spectrum = np.fft.rfft(s_fund, n=n_fft)
        freqs = np.fft.rfftfreq(n_fft, 1 / self.fs)
        power = np.abs(spectrum) ** 2
        # Estimate a typical well-excited level from the upper part of the
        # sweep. The floor is 10^-6 of that *power* (60 dB down), with a
        # tiny absolute minimum to avoid division by zero.
        reference_band = (freqs >= 0.2 * self.fs) & (freqs <= 0.4 * self.fs)
        power_floor = max(float(np.median(power[reference_band])) * 1e-6, 1e-30)
        self.inverse_origin_sample = (n_fft + n) // 2 - 1
        # conj(S)/(|S|^2+floor) is a stable approximation to 1/S. It avoids
        # huge amplification at frequencies where the sweep has little energy.
        inverse_spectrum = np.conj(spectrum) / (power + power_floor)
        # Retain the complete numerical inverse. Cutting it back to the sweep
        # length discards samples needed to cancel a low-level swept tail.
        # Its time origin is independent of its support and must accompany
        # the array when processing or archiving it.
        periodic = np.fft.irfft(inverse_spectrum, n=n_fft)
        inv, self.inverse_origin_sample = _full_inverse(
            periodic, negative_samples=self.inverse_origin_sample)
        return s_fund, phase, inv


class HarmonicInjector:
    """Injects H2/H3 distortions using the sweep's phase array."""

    def __init__(self, h2_db: Optional[float] = None, h3_db: Optional[float] = None) -> None:
        """
        Initialize the harmonic injector.

        :param h2_db: Level of H2 in dB relative to fundamental.
        :param h3_db: Level of H3 in dB relative to fundamental.
        """
        self.h2_db = h2_db
        self.h3_db = h3_db

    def inject(
        self, s_fund: np.ndarray, phase: np.ndarray, *,
        envelope: Optional[np.ndarray] = None,
        instantaneous_hz: Optional[np.ndarray] = None,
        fs: Optional[int] = None,
    ) -> np.ndarray:
        """Inject separated harmonics without folding ultrasonic test tones back.

        The capture path supplies the sweep envelope and instantaneous frequency.
        The two-argument form is retained for existing external callers.
        """
        s_composite = s_fund.copy()
        for order, level in ((2, self.h2_db), (3, self.h3_db)):
            if level is None:
                continue
            # The test harmonic follows the same start/end fade as the sweep.
            weight = np.ones_like(s_fund) if envelope is None else envelope.copy()
            if instantaneous_hz is not None and fs is not None:
                # A generated H2/H3 frequency can cross Nyquist (fs/2). Above
                # Nyquist it folds into false low-frequency audio: aliasing.
                # Fade each test harmonic out before that crossing.
                start, end = 0.875 * fs / 2, 0.96 * fs / 2
                position = np.clip((order * instantaneous_hz - start) / (end - start), 0, 1)
                weight *= 0.5 * (1 + np.cos(np.pi * position))
            logger.info(f"Injecting H{order} @ {level} dB")
            # sin(order * phase) tracks harmonic order without changing the
            # time base; dB is converted to a linear amplitude multiplier.
            s_composite += DSPUtils.db_to_lin(level) * np.sin(order * phase) * weight
        return s_composite


class ProtectionFilter:
    """Applies MIN phase HPF to protect drivers, and generates an inverse correction mask."""

    def __init__(self, fs: int, freq_hz: float, order: int, hpf_correction: bool = False, hpf_corr_db_cap: float = 12.0) -> None:
        """
        Initialize the protection filter.

        :param fs: Sample rate (Hz).
        :param freq_hz: Cutoff frequency (Hz).
        :param order: Filter order.
        :param hpf_correction: If True, generates an inverse mask for deconvolution.
        :param hpf_corr_db_cap: Maximum magnitude gain applied during correction.
        """
        self.fs = fs
        self.freq_hz = freq_hz
        self.order = order
        self.hpf_correction = hpf_correction
        self.hpf_corr_db_cap = hpf_corr_db_cap

        if self.freq_hz is not None and self.freq_hz > 0:
            # Minimum Phase: Standard IIR Butterworth (SOS implementation for stability)
            self.sos = scipy.signal.butter(self.order, self.freq_hz, btype='hp', fs=self.fs, output='sos')
        else:
            self.sos = None

    def apply(self, sig: np.ndarray) -> np.ndarray:
        """Applies the High Pass Protection Filter to the playback signal."""
        if self.sos is None:
            return sig

        logger.debug(f"► Applying Protection HPF: {self.freq_hz}Hz, Order={self.order} (Min Phase)")
        return scipy.signal.sosfilt(self.sos, sig).astype(np.float32)

    def get_correction_mask(self, n_bins: int) -> np.ndarray:
        """
        Generates the driver protection HPF frequency-domain correction mask for the deconvolution engine.
        Hard-caps the magnitude gain.
        """
        if self.sos is None or not self.hpf_correction:
            return np.ones(n_bins, dtype=np.complex64)

        logger.debug(f"► Generating HPF Correction Mask (Max Gain: +{self.hpf_corr_db_cap}dB)")

        # 1. Calculate the exact complex frequency response of the applied IIR filter
        _, H = scipy.signal.sosfreqz(self.sos, worN=n_bins, fs=self.fs)

        # 2. Capped Magnitude Inversion
        mag_h = np.abs(H)
        inv_mag = 1.0 / np.maximum(mag_h, 1e-12)  # Avoid divide by zero

        max_gain_lin = DSPUtils.db_to_lin(self.hpf_corr_db_cap)
        inv_mag_capped = np.minimum(inv_mag, max_gain_lin)

        # 3. Minimum Phase Reconstruction (Cepstrum Method)
        # We calculate the minimum phase response corresponding to the capped magnitude
        # so that the correction remains causal and avoids LF pre-ringing.
        Nfft = (n_bins - 1) * 2  # Recover the original even Nfft size
        log_mag = np.log(np.maximum(inv_mag_capped, 1e-12))
        log_mag_full = np.concatenate([log_mag, log_mag[-2:0:-1]])

        cepstrum = np.fft.ifft(log_mag_full).real
        w = np.zeros(Nfft)
        w[0] = 1.0
        mid = Nfft // 2
        w[mid] = 1.0
        w[1:mid] = 2.0

        H_corr = np.exp(np.fft.fft(cepstrum * w))[:n_bins].astype(np.complex64)

        # 4. Safety kill at exact DC (0 Hz) to prevent any baseline wander blowout
        H_corr[0] = 0.0 + 0.0j

        return H_corr


# ─────────────────────────────────────────────────────────────────────────────
#  PROCESSING ENGINES
# ─────────────────────────────────────────────────────────────────────────────

class AlignmentEngine:
    """Handles cross-correlation and synchronous averaging."""

    def __init__(self, fs: int, num_sweeps: int, align_to_first_marker: bool, mic_tail_taper_ms: float,
                 marker_dur_ms: float):
        """
        Initialize the alignment engine.

        :param fs: Sample rate (Hz).
        :param num_sweeps: Number of sweeps to average.
        :param align_to_first_marker: If True, align only to the first marker.
        :param mic_tail_taper_ms: Duration of the tail taper in milliseconds.
        :param marker_dur_ms: Duration of the marker in milliseconds.
        """
        self.fs = fs
        self.num_sweeps = num_sweeps
        self.align_to_first_marker = align_to_first_marker
        self.mic_tail_taper_ms = mic_tail_taper_ms
        self.marker_dur_ms = marker_dur_ms
        self.last_psr_reference = 0.0

    def _calculate_psr(self, lags: np.ndarray, corr: np.ndarray, peak_lag: int,
                       peak_val: float, ref_len: int) -> float:
        """Compare a selected peak with the marker's surrounding correlation peaks."""
        chip_dur_s = (self.marker_dur_ms / 1000.0) / 13.0
        exclusion_samps = int((chip_dur_s * 2.0) * self.fs)
        quality_start = max(0, peak_lag - ref_len)
        quality_end = min(int(lags[-1]), peak_lag + ref_len)
        sidelobe_windows = (
            (lags >= quality_start) & (lags <= quality_end)
            & (np.abs(lags - peak_lag) > exclusion_samps)
        )
        sidelobe_val = float(np.max(corr[sidelobe_windows], initial=0.0))
        return peak_val / (sidelobe_val + 1e-12) if sidelobe_val > 0 else 0.0


    def reference_psr(self, ref: np.ndarray) -> float:
        """Return the ideal digital-loopback PSR for the current marker waveform."""
        marker_start = len(ref)
        ideal_recording = np.pad(ref, (marker_start, marker_start))
        lags, corr = DSPUtils.rfft_xcorr(ideal_recording, ref)
        peak_val = float(corr[lags == marker_start][0])
        return self._calculate_psr(lags, corr, marker_start, peak_val, len(ref))


    def _format_marker_clarity(self, psr: float) -> str:
        """Format the internal ratio as the user-facing dB and relative score."""
        measured_db = float(20.0 * np.log10(psr)) if psr > 0 else 0.0
        if self.last_psr_reference > 1.0:
            reference_db = float(20.0 * np.log10(self.last_psr_reference))
            score = float(np.clip(10.0 * measured_db / reference_db, 0.0, 10.0))
            return f"{measured_db:.1f} dB / {reference_db:.1f} dB reference ({score:.1f}/10)"
        return f"{measured_db:.1f} dB"


    @staticmethod
    def _marker_scores(x: np.ndarray, ref: np.ndarray, normalize: bool):
        lags, corr = DSPUtils.rfft_xcorr(x, ref)
        scores = corr
        if normalize:
            # The following ESS can have greater energy than the short marker.
            # Normalise each complete candidate window so a loud portion of
            # the sweep cannot beat a genuine match purely through amplitude.
            # A running sum lets us get any window's squared energy by
            # subtraction, without re-summing each candidate from scratch.
            energy_sum = np.concatenate(([0.0], np.cumsum(np.square(x, dtype=np.float64))))
            starts = np.clip(lags, 0, len(x))
            ends = np.clip(lags + len(ref), 0, len(x))
            energy = np.maximum(energy_sum[ends] - energy_sum[starts], 0.0)
            # Ignore effectively silent windows: FFT roundoff there can make
            # a near-zero numerator and denominator look like a perfect match.
            energy_floor = max(float(np.max(energy)) * 1e-12, np.finfo(float).tiny)
            denom = np.sqrt(energy * np.sum(np.square(ref, dtype=np.float64)))
            scores = np.divide(corr, denom, out=np.zeros_like(corr), where=(energy > energy_floor) & (denom > 0))

        return lags, corr, scores

    def _matched_filter_detect(self, x: np.ndarray, ref: np.ndarray, search_start: int = None,
                               search_end: int = None, normalize: bool = False) -> Tuple[int, float, float]:
        """
        Find the marker template ``ref`` in recording ``x``.

        Return the sample index, raw correlation height, and peak/sidelobe
        ratio (PSR). With normalization, compare the *shape* of each window
        rather than choosing whichever window simply has the largest level.
        """
        # Cross-correlation slides the known marker across the recording. A
        # high score means the local recording looks like the marker.
        lags, corr, scores = self._marker_scores(x, ref, normalize)
        if search_start is None:
            search_start = 0
        if search_end is None:
            search_end = len(x) - 1
        # Only test windows that contain every sample of the marker.
        search_end = min(search_end, len(x) - len(ref))

        # Search only where this marker is expected on the recording timeline.
        m = (lags >= search_start) & (lags <= search_end)
        lags_sel, corr_sel = lags[m], scores[m]

        if len(lags_sel) == 0:
            return 0, 0.0, 0.0
        i = int(np.argmax(corr_sel))
        # Preserve the old raw-correlation return value for callers, while
        # using the normalized score to decide whether the marker is credible.
        peak_val = float(corr[m][i])
        peak_score = float(corr_sel[i])
        if normalize and peak_score < 0.5:
            raise RuntimeError(f"No reliable Barker marker found (normalised correlation {peak_score:.3f})")

        peak_lag = int(lags_sel[i])
        # Normalize only marker selection. Preserve master's raw-correlation
        # clarity scale and compare it with the same raw ideal reference.
        complete = (lags >= 0) & (lags <= len(x) - len(ref))
        psr = self._calculate_psr(lags[complete], corr[complete], peak_lag, peak_val, len(ref))

        # A small ratio warns that the chosen marker is not clearly isolated.
        # It does not by itself prove phase smearing in the acoustic path.
        if psr < 2.5:
            logger.warning(f"Marker clarity is {self._format_marker_clarity(psr)}; check alignment quality.")
        # --- END QUALITY / PSR CHECK ---


        return int(lags_sel[i]), peak_val, psr

    def sync_and_average(self, rec_mic: np.ndarray, rec_loop: np.ndarray, marker_single: np.ndarray,
                         pre_samps_settle: int, slot_len: int, sweep_len: int,
                         sweep_offset_samples: int = 0,
                         capture_tail_samples: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray, List[np.ndarray], float]:
        """
        Align multiple sweeps and average them to increase SNR.

        :param rec_mic: Recorded microphone signal.
        :param rec_loop: Recorded loopback signal.
        :param marker_single: The reference marker signal.
        :param pre_samps_settle: Number of samples to skip at the beginning.
        :param slot_len: Total length of one sweep slot (including silence/markers).
        :param sweep_len: Length of the actual sweep signal.
        :param sweep_offset_samples: Samples from marker start to sweep-array start.
        :param capture_tail_samples: Recorded post-silence to retain. New captures
            pass this explicitly rather than using the legacy tail setting.
        :return: Tuple of (averaged mic, averaged loop, individual mic slices, PSR).
        """

        # Each aligned window starts at the sweep, not at its preceding marker.
        # Include the chosen microphone tail so reflections can be measured.
        mic_slices = []
        loop_slices = []
        self.last_alignment = []
        self.last_psr_reference = self.reference_psr(marker_single)
        # New captures pass the full post-sweep interval explicitly. Retain the
        # old constructor setting only for callers using the legacy API.
        tail_samples = (int(round(self.mic_tail_taper_ms / 1000.0 * self.fs))
                        if capture_tail_samples is None else capture_tail_samples)
        capture_len = sweep_len + tail_samples

        # Find the first marker in its expected slot. Stop before the next
        # marker, even if the interface has nearly zero transport latency.
        search_limit = pre_samps_settle + slot_len - len(marker_single)
        k_first_marker, _, psr = self._matched_filter_detect(
            rec_loop, marker_single, search_end=search_limit, normalize=sweep_offset_samples > 0,
        )

        # Detect markers in the recording timeline; offset both channels by
        # exactly the same amount to preserve acoustic delay and phase.
        logger.debug(f"Marker found at {k_first_marker}. Sweep offset={sweep_offset_samples}. PSR={psr:.1f}")

        # Later markers should lie one complete slot apart. A small search
        # around each prediction tolerates timing drift between repeats.
        window_samps = int(0.005 * self.fs)  # 5 ms each side

        for i in range(self.num_sweeps):
            expected_t0 = k_first_marker + (i * slot_len)

            if self.align_to_first_marker:
                # Sample-based cut: Rely on the first marker and constant sample rate
                marker_idx = expected_t0
                psr_local = psr
            else:
                # Per-sweep alignment: Re-sync to the marker for *every* sweep
                # Corrects for minor clock drift in very long sequences
                s_start = max(0, expected_t0 - window_samps)
                s_end = min(len(rec_loop), expected_t0 + window_samps)
                k_local, _, psr_local = self._matched_filter_detect(rec_loop, marker_single, search_start=s_start,
                                                                    search_end=s_end, normalize=sweep_offset_samples > 0)
                marker_idx = k_local

            # Skip over the marker and silent gap on *both* recorded channels.
            # Applying the same index preserves the microphone's real delay.
            start_idx = marker_idx + sweep_offset_samples
            end_idx = start_idx + capture_len
            # Copy each complete window for averaging and offline inspection.
            if end_idx <= min(len(rec_mic), len(rec_loop)) and start_idx >= 0:
                mic_slices.append(rec_mic[start_idx: end_idx].copy())
                loop_slices.append(rec_loop[start_idx: end_idx].copy())
                self.last_alignment.append({
                    "repeat": i, "marker_start_sample": int(marker_idx),
                    "sweep_start_sample": int(start_idx), "capture_end_sample": int(end_idx),
                    "psr": float(psr_local),
                })
            else:
                raise RuntimeError(f"Capture ended before sweep {i + 1} and its tail were fully recorded")

        if not mic_slices:
            raise RuntimeError("No valid sweeps captured (Alignment failed).")

        # One ambiguous repeat can spoil an average. Report the weakest
        # assessed repeat rather than silently reporting only the first.
        psr = min(row["psr"] for row in self.last_alignment)

        # Synchronous Averaging to lower the noise floor
        # By averaging multiple sweeps, the correlated signal (the sweep) adds coherently,
        # while the uncorrelated noise (background noise) adds incoherently, improving SNR.
        avg_mic = np.mean(mic_slices, axis=0)
        avg_loop = np.mean(loop_slices, axis=0)

        # Ease out the microphone tail before deconvolution, avoiding a hard
        # cut. Keep the electrical sweep un-faded for future analysis.
        # Do not let a short/zero post interval fade the sweep itself.
        fade_ms = min(10.0, tail_samples * 1000.0 / self.fs)
        if fade_ms > 0:
            avg_mic = DSPUtils.hann_fade(avg_mic, fade_ms, self.fs, side="out")

        return avg_mic.astype(np.float32), avg_loop.astype(np.float32), mic_slices, psr


class DeconvolutionEngine:
    """Turn a recorded sweep into its linear and harmonic impulse responses."""

    def __init__(self, fs: int) -> None:
        """
        Initialize the deconvolution engine.

        :param fs: The sample rate (Hz).
        """
        self.fs = fs
        self.last_loopback_correction = {"enabled": False}

    @staticmethod
    def _inverse_layout(inv_data, inverse_origin_sample=None, sweep_samples=None):
        """Resolve explicit timing; omitted fields preserve legacy archives."""
        if inverse_origin_sample is not None and sweep_samples is None:
            raise ValueError("Explicit inverse origin requires sweep_samples")
        origin = len(inv_data) - 1 if inverse_origin_sample is None else inverse_origin_sample
        length = origin + 1 if sweep_samples is None else sweep_samples
        if (not isinstance(origin, (int, np.integer)) or origin < _LINEAR_IR_ORIGIN_SAMPLE or origin >= len(inv_data)
            or not isinstance(length, (int, np.integer)) or length < _LINEAR_IR_ORIGIN_SAMPLE + 1 or origin < length - 1):
            raise ValueError("Invalid inverse origin or sweep length")
        return int(origin), int(length)

    def _loopback_kernel(self, loop_data, reference_data, inv_data, log_sweep_rate, *,
                         inverse_origin_sample=None, sweep_samples=None, loop_valid_samples=None):
        # Retry on a larger FFT only when the LF gain check needs finer resolution.
        # Keep each attempt in a separate call so its large arrays can be released.
        for refinement in range(4):
            kernel, advance = self._build_loopback_kernel(
                loop_data, reference_data, inv_data, log_sweep_rate,
                inverse_origin_sample=inverse_origin_sample, sweep_samples=sweep_samples,
                loop_valid_samples=loop_valid_samples, refinement=refinement)
            if kernel is not None:
                return kernel, advance
        raise RuntimeError("Loopback correction did not resolve its LF support")

    def _correction_ceiling(self, freqs: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Return the permitted gain and soft-limiter order at each frequency."""
        # Retain 12 dB through 20 kHz, then taper to 6 dB at Nyquist.
        # Quintic smoothstep has zero first/second derivatives at both joins;
        # soften the limiter exponent from 4 to 2 over that same interval.
        # At sample rates <= 40 kHz there is no band above 20 kHz to taper.
        if self.fs > 40000:
            u = np.clip((freqs - 20000.0) / (self.fs / 2 - 20000.0), 0.0, 1.0)
        else:
            u = np.zeros_like(freqs)
        # u runs from 0 at 20 kHz to 1 at Nyquist; clipping preserves the passband.
        shape = u**3 * (10 - 15*u + 6*u*u)  # Smooth joins avoid an abrupt knee.
        shape_order = 4 - 2*shape  # Smaller order gives a gentler limiting transition.
        cap_profile = 10 ** ((12 - 6*shape) / 20)  # Convert the dB ceiling to amplitude gain.
        return cap_profile, shape_order

    def _residual_phase(self, target_magnitude: np.ndarray, ratio: np.ndarray,
                        freqs: np.ndarray, nfft: int) -> Tuple[np.ndarray, int]:
        """Give only the residual bandwidth filter minimum phase, handling DC zeros.

        The interface's measured phase is inverted separately by the caller.
        A cepstrum is the inverse FFT of log magnitude; making it one-sided
        constructs the phase associated with a minimum-phase response.
        """
        log_target = np.log(np.maximum(target_magnitude, 1e-15))  # Keep log(0) finite.
        # Factor any resolved DC zero out before the real cepstrum. Its
        # logarithm is singular; flooring that one bin invents a periodic
        # LF tail. Reinsert the exact phase of (1-z^-1)**order afterwards.
        # The first two nonzero frequencies are an octave apart. Their slope
        # estimates how many DC zeros are present, only when DC is strongly reduced.
        slope = (log_target[2] - log_target[1]) / np.log(2)
        dc_order = int(np.clip(round(slope), 0, 4)) if ratio[0] < .1*ratio[1] else 0
        dc_phase = np.zeros_like(freqs)
        if dc_order:
            omega = 2*np.pi*freqs/self.fs  # Frequency in radians per sample.
            # Remove the known zero analytically before estimating the smooth remainder.
            log_target[1:] -= dc_order*np.log(2*np.sin(omega[1:]/2))
            log_target[0] = (4*log_target[1]-log_target[2])/3  # Extrapolate that remainder to DC.
            dc_phase = dc_order*(np.pi/2-omega/2)  # Restore the zero's known phase later.
        cepstrum = np.fft.irfft(log_target, nfft)
        cepstrum[1:nfft // 2] *= 2  # Fold negative-time content onto positive time.
        cepstrum[nfft // 2 + 1:] = 0  # Leave DC and the midpoint unchanged.
        target_phase = np.imag(np.fft.rfft(cepstrum)) + dc_phase
        return target_phase, dc_order

    def _build_loopback_kernel(self, loop_data, reference_data, inv_data, log_sweep_rate, *,
                              inverse_origin_sample, sweep_samples, loop_valid_samples, refinement):
        """Estimate only the reference's linear response, then invert its shape.

        Both inputs have the SAME marker-derived origin as the microphone.
        Never peak-align the microphone or invert the full reference response:
        the latter would subtract its harmonics from the DUT's harmonics.
        """
        # 1. Validate aligned recordings and exclude any following timing marker.
        if not np.isfinite(log_sweep_rate) or log_sweep_rate <= 0:
            raise ValueError("Loopback correction needs a positive ESS log sweep rate")
        loop = np.asarray(loop_data, dtype=np.float64)
        reference = np.asarray(reference_data, dtype=np.float64)
        center, sweep_samples = self._inverse_layout(inv_data, inverse_origin_sample, sweep_samples)
        if (loop.ndim != 1 or reference.ndim != 1 or len(loop) < sweep_samples
                or len(reference) < sweep_samples
                or not np.all(np.isfinite(loop)) or not np.all(np.isfinite(reference))):
            raise ValueError("Loopback correction needs finite, complete aligned sweeps")
        if loop_valid_samples is not None:
            if not isinstance(loop_valid_samples, (int, np.integer)) or not sweep_samples <= loop_valid_samples <= len(loop):
                raise ValueError("Invalid clean loopback capture length")
            # The next marker can arrive slightly early between repeats.
            # Keep raw archives intact; exclude its known arrival here only.
            loop = loop[:loop_valid_samples].copy()
            fade = min(round(.010*self.fs), loop_valid_samples-sweep_samples)
            if fade > 1:
                loop[-fade:] *= .5*(1+np.cos(np.linspace(0,np.pi,fade)))
        # 2. Select the loopback's linear IR, retaining its long causal LF tail.
        # H2 constrains the NEGATIVE side only. A symmetric short window cuts
        # AC-coupling tails and invents LF ripple in the reference estimate.
        # Keep its causal tail; room responses are never windowed here.
        before = min(round(.010 * self.fs), int(.35 * log_sweep_rate * np.log(2) * self.fs))
        # Retain the same nominal post-origin duration as a linear IR export.
        # A longer recorded decay may extend it further. This is available
        # deconvolved support, not a promise of fully excited HF at late times.
        after = max(sweep_samples, len(loop) - sweep_samples)
        after = min(after, min(len(loop), len(reference)) + len(inv_data) - center - 2)
        if before < 8:
            raise ValueError("Sweep is too short to isolate the loopback linear response")
        gate = np.ones(before + after + 1)
        for count, side in ((before // 4, 'in'), (max(2, after // 20), 'out')):
            taper = scipy.signal.windows.hann(2 * count)
            if side == 'in':
                gate[:count] = taper[:count]
            else:
                gate[-count:] = taper[count:]
        # 3. Deconvolve the hardware recording and digital reference identically.
        # Give the correction its own time support and a finer LF grid. It
        # need not fit inside the measured H1 window; leave space on both sides
        # for its inverse, independently of harmonic isolation.
        nfft = scipy.fft.next_fast_len(max(max(len(loop), len(reference)) + len(inv_data) - 1,
                                        round(8.0 * self.fs), 4 * (before + after + 1)))
        nfft *= 2**refinement
        nfft += nfft % 2  # even length for the real cepstrum below
        inv = np.fft.rfft(inv_data, n=nfft)

        def isolated(samples):
            # This local helper deliberately shares the exact same inverse and gate.
            h = np.fft.irfft(np.fft.rfft(samples, n=nfft) * inv, n=nfft)
            segment = h[center - before:center + after + 1] * gate
            # Zero phase at the ORIGINAL nominal impulse index, not a
            # detected peak. This retains differential acoustic delay.
            return np.fft.rfft(segment, n=nfft) * np.exp(
                2j * np.pi * np.fft.rfftfreq(nfft) * before)

        loop_spectrum, reference_spectrum = isolated(loop), isolated(reference)
        # 4. Separate interface shape from absolute recording/playback gain.
        freqs = np.fft.rfftfreq(nfft, 1 / self.fs)
        mid = (freqs >= 700) & (freqs <= 1300)
        ref_level = float(np.median(np.abs(reference_spectrum[mid])))
        loop_level = float(np.median(np.abs(loop_spectrum[mid])))
        if ref_level < 1e-10 or loop_level < max(1e-10, ref_level * 1e-5):
            raise ValueError("Loopback sweep is missing or too weak for correction")
        # Normalize SHAPE only, retaining microphone gain / RMS calibration.
        # reference_spectrum also retains the digital inverse's finite-band response: don't
        # accidentally 'correct' the sweep fade or invert its unexcited bins.
        gain = float(np.median(np.abs(loop_spectrum[mid]) / np.maximum(np.abs(reference_spectrum[mid]), 1e-30)))
        # Smoothly limit inverse gain. The residual bandwidth is intentional:
        # recovering a converter stopband would amplify noise, not information.
        # A real, zero-phase limiter leaves a two-sided impulse whose precursor
        # is chopped by the sample-50 export. Give ONLY this regularization
        # response minimum phase, not the measured loudspeaker/room response.
        scale = gain * np.maximum(np.abs(reference_spectrum), ref_level * 1e-4)
        ratio = np.abs(loop_spectrum) / scale
        cap_profile, shape_order = self._correction_ceiling(freqs)
        # 5. Design, then verify the actual inverse; lower the design ceiling if needed.
        design_cap = cap_profile.copy()  # The acceptance ceiling remains unchanged.
        for _ in range(32):
            # Scaling before exponentiation keeps the soft limiter numerically stable.
            denominator = np.maximum(ratio, 1.0 / design_cap)
            denominator *= ((ratio / denominator) ** shape_order
                            + (1.0 / design_cap / denominator) ** shape_order) ** (1 / shape_order)
            # A frequency-dependent limiter must not colour a unity interface.
            # Normalize its residual magnitude BEFORE deriving its phase.
            unity_target = (1 + design_cap ** (-shape_order)) ** (-1 / shape_order)
            target_magnitude = ratio / denominator / unity_target
            target_phase, dc_order = self._residual_phase(target_magnitude, ratio, freqs, nfft)
            inverse_magnitude = (gain * np.abs(reference_spectrum) / scale) / denominator / unity_target
            # Undo only the measured interface phase, then add the residual filter phase.
            correction_spectrum = inverse_magnitude * np.exp(
                1j * (np.angle(reference_spectrum) - np.angle(loop_spectrum) + target_phase))
            if dc_order:
                correction_spectrum[0] = np.copysign(inverse_magnitude[0], correction_spectrum[1].real)
            periodic_inverse = np.fft.irfft(correction_spectrum, n=nfft)
            # Finite, two-sided linear convolution cannot wrap a room echo.
            kernel, advance = _full_inverse(periodic_inverse)
            response = np.fft.rfft(kernel, n=nfft)
            normalization = max(float(np.median(np.abs(response[mid]))), 1e-30)
            kernel /= normalization  # Preserve midband calibration rather than peak-normalizing.
            # A long FIR can peak BETWEEN design bins. Check a four-times
            # denser grid before accepting the realised gain ceiling.
            checked = np.abs(np.fft.rfft(kernel, n=4*nfft))
            checked_cap = np.interp(np.arange(len(checked)) / 4,
                                    np.arange(len(cap_profile)), cap_profile)
            finite_peak = float(np.max(checked))
            excess = float(np.max(checked / checked_cap))
            peak_frequency = np.argmax(checked / checked_cap) * self.fs / (4*nfft)
            if excess > 1 and peak_frequency < 20 and nfft < 64*self.fs and refinement < 3:
                # A slowly decaying LF inverse can meet the limit on FFT bins
                # while exceeding it between them if its period is too short.
                # Resolve that tail before reducing gain across the passband.
                # Returning releases these large arrays before the next grid.
                return None, 0
            # Leave 0.1 dB for peaks between verification bins. The independent
            # regression grid is twice as dense again; unity is unaffected.
            verification_headroom = 10 ** (-0.1 / 20)
            if excess <= verification_headroom:
                break
            # Measured-IR windowing and gain normalization can overshoot the cap,
            # especially with short sweeps. Redesign smoothly with headroom;
            # don't clip the spectrum again or change the calibrated gain.
            design_cap *= .995 * verification_headroom / excess
        else:
            raise ValueError("Loopback correction could not produce a safely bounded inverse")
        # 6. Archive the design decisions so a saved measurement remains explainable.
        self.last_loopback_correction = {
            "enabled": True, "method": "linear_loopback_v4",
            "gate_before_samples": before, "gate_after_samples": after,
            "kernel_samples": len(kernel), "estimation_fft_samples": nfft,
            "lf_grid_refinements": refinement,
            "kernel_before_samples": advance, "kernel_after_samples": len(kernel)-advance-1,
            "kernel_truncation_l1_bound": 0.0,
            "dc_zero_order": dc_order, "loop_valid_samples": len(loop),
            "reference_midband_gain": gain, "normalization_hz": [700, 1300],
            "max_boost_db": 12.0, "regularization": "smooth_bounded_minimum_phase_target",
            "regularization_order": 4,
            "hf_regularization_order": 2,
            "ceiling_profile": "quintic_12_to_6_db",
            "ceiling_taper_start_hz": 20000.0,
            "ceiling_nyquist_db": 6.0 if self.fs > 40000 else 12.0,
            "design_max_boost_db": float(20 * np.log10(np.max(design_cap))),
            "finite_kernel_max_boost_db": float(20 * np.log10(max(finite_peak, 1e-30))),
            "dc_protection_hz": 0.0,
            "harmonic_reference": "isolated_linear_only",
        }
        return kernel, advance

    def process_ir(self, mic_data: np.ndarray, inv_data: np.ndarray, protection_filter: Optional[ProtectionFilter] = None,
                   linear_ir_samples: Optional[int] = None, *,
                   loop_data: Optional[np.ndarray] = None,
                   reference_data: Optional[np.ndarray] = None,
                   log_sweep_rate: Optional[float] = None,
                   inverse_origin_sample: Optional[int] = None,
                   sweep_samples: Optional[int] = None,
                   loop_valid_samples: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Performs deconvolution to extract the Impulse Response.

        linear_ir_samples optionally extends the exported linear window for
        room responses longer than the sweep. The nominal timing origin remains sample 50; the peak may occur later.
        Supplying loop_data, reference_data and log_sweep_rate enables linear
        loopback correction of both returned records. Omitting them retains
        the original digital-only result. Reference data is the actual clean
        digital playback, before any speaker-only protection filter.
        Generated extended inverses require inverse_origin_sample from their
        SweepGenerator and sweep_samples (the playback length). This controls
        the default export length and
        fade duration independently of inverse support. Omitted timing uses
        the legacy origin len(inverse)-1 and a sweep length of origin+1.
        
        Processing order:
          1. Resolve timing and allocate enough FFT space for linear convolution.
          2. Apply the digital sweep inverse and optional driver compensation.
          3. Apply the isolated linear-loopback correction, when requested.
          4. Remove processing-only delay and convert to the IR amplitude convention.
          5. Select/fade the full export and slice out its linear response.

        Returns:
            ir_full:   The full time-domain result containing linear IR + distortion echoes.
            ir_linear: The cropped linear response (causal part).
        """
        self.last_loopback_correction = {"enabled": False}
        origin, sweep_samples = self._inverse_layout(inv_data, inverse_origin_sample, sweep_samples)
        if loop_data is not None and (reference_data is None or log_sweep_rate is None):
            raise ValueError("Loopback correction requires the digital reference and ESS sweep rate")
        # Convolving arrays of lengths a and b gives a+b-1 samples. Padding
        # the FFT to at least that size prevents circular wraparound.
        n_conv = len(mic_data) + len(inv_data) - 1
        nfft = int(2 ** np.ceil(np.log2(n_conv)))

        # 1. Transform both complete arrays onto the same padded frequency grid.
        # rfft (real FFT) is used since our time-domain signals are purely real, 
        # which is faster and saves memory compared to a full complex FFT.
        mic_spectrum = np.fft.rfft(mic_data, n=nfft)
        inverse_spectrum = np.fft.rfft(inv_data, n=nfft)

        # Optionally compensate the *speaker protection* high-pass filter.
        # This is separate from the electrical reference's linear correction.
        if protection_filter:
            hpf_mask = protection_filter.get_correction_mask(len(inverse_spectrum))
            inverse_spectrum = inverse_spectrum * hpf_mask

        # 2. Recover the uncorrected full IR, including earlier harmonic responses.
        # Multiply spectra, then transform back: this is convolution of the
        # microphone sweep with the inverse prepared by SweepGenerator.
        # Padding covers both complete finite records.
        h_full = np.fft.irfft(mic_spectrum * inverse_spectrum, n=nfft)[:n_conv]
        # 3. Apply the separately designed linear interface correction, if requested.
        if loop_data is not None:
            kernel, advance = self._loopback_kernel(
                loop_data, reference_data, inv_data, log_sweep_rate,
                inverse_origin_sample=origin, sweep_samples=sweep_samples,
                loop_valid_samples=loop_valid_samples)
            h_full = scipy.signal.fftconvolve(h_full, kernel, mode='full')[advance:advance + n_conv]
        # 4. Restore the public timing convention without searching for the DUT peak.
        # Discard only the additional pre-history used by the inverse. The
        # returned full record retains its sweep_samples-1 time origin, so
        # Farina windows, marker timing and exported file lengths do not move.
        h_full = h_full[origin - (sweep_samples - 1):]
        origin = sweep_samples - 1
        h_full = h_full.astype(np.float32)
        # No additional near-Nyquist mask: the matched ESS inverse controls the
        # deconvolution residual without imposing an extra 20 kHz decay shoulder.
        # Playback sweep level remains a peak dBFS safety limit. Calibration and
        # downstream SPL interpretation are RMS-based, so returned IRs use a
        # sine-RMS amplitude convention.
        h_full *= SINE_RMS_FROM_PEAK

        return self._export_records(h_full, origin, sweep_samples, linear_ir_samples)

    def _export_records(self, h_full: np.ndarray, origin: int, sweep_samples: int,
                        linear_ir_samples: Optional[int]) -> Tuple[np.ndarray, np.ndarray]:
        """Apply file-length/fade policy and select the linear record at a fixed origin.

        This receives a fully deconvolved record. It never searches for a peak:
        doing so would erase real DUT delay and shift separated harmonics.
        """
        # --- WINDOWING & SEPARATION of Linear and Distortion IRs ---

        # Preserve the existing full-record length and end fade. Energy near
        # the far end can come from where finite sweep/filter records meet;
        # do not confuse it with early acoustic reflections or distortion.
        # Longer requested room responses need a longer export as well as a
        # longer recorded input. Legacy callers keep the existing lengths.
        linear_length = sweep_samples if linear_ir_samples is None else int(linear_ir_samples)
        if linear_length < 1:
            raise ValueError("linear_ir_samples must be positive")
        fade_samples = int(round(sweep_samples * _EXPORT_FADE_FRACTION))
        full_length = origin + 1 + sweep_samples
        if linear_length > sweep_samples:
            full_length = max(full_length,
                              origin - _LINEAR_IR_ORIGIN_SAMPLE + linear_length + fade_samples)
        h_full = h_full[:full_length]

        # This expression converts 10% of one sweep from seconds to ms.
        fade_ms = (sweep_samples / self.fs) * (_EXPORT_FADE_FRACTION * 1000.0)
        # A smooth exit avoids a new sharp edge at the stored file's end.
        h_full = DSPUtils.hann_fade(h_full, fade_ms, self.fs, side="out")

        # The nominal linear origin is explicit; acoustic delay and
        # residual filtering can move its peak. Keep the fixed marker-derived
        # origin at export sample 50, leaving room for hardware pre-ringing.
        # Earlier Farina harmonic peaks stay in h_full, outside h_linear.
        split_idx = origin - _LINEAR_IR_ORIGIN_SAMPLE
        h_linear = h_full[split_idx: split_idx + linear_length]
        if len(h_linear) != linear_length:
            raise RuntimeError("Recorded input is too short for the requested IR duration")

        return h_full, h_linear


# ─────────────────────────────────────────────────────────────────────────────
#  INTERFACES
# ─────────────────────────────────────────────────────────────────────────────

class IAudio(ABC):
    @abstractmethod
    def measure_ir(self, position: CylindricalPosition, order_id: str = "NA", save: bool = True) -> dict[str, Any]:
        pass

    @abstractmethod
    def play_sine(self, frequency: float, level_dbfs: float, duration_s: Optional[float] = 1.0) -> None:
        """
        Plays a sine wave at the specified frequency and level.

        Both finite and continuous tones bypass the driver protection filter;
        that filter belongs to sweep playback only.

        :param frequency: Frequency in Hz.
        :param level_dbfs: Level in dBFS.
        :param duration_s: Duration in seconds. If None, plays until stop_sine() is called.
        """
        pass

    @abstractmethod
    def stop_sine(self) -> None:
        """
        Stops the sine wave playback.
        """
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

class Audio(IAudio):
    """Orchestrates signal generation, streaming, and deconvolution."""

    def __init__(self,
                 hw_config: Dict[str, Any],
                 capture_config: Dict[str, Any],
                 sweep_gen: SweepGenerator,
                 marker_gen: MarkerGenerator,
                 alignment_engine: AlignmentEngine,
                 deconv_engine: DeconvolutionEngine,
                 harmonic_injector: Optional[HarmonicInjector] = None,
                 protection_filter: Optional[ProtectionFilter] = None):

        self.hw = hw_config
        self.cap = capture_config
        self._sine_stream = None
        self._sine_com_context = None
        self._sine_owner_thread = None
        self._sine_meter_worker: _AudioMeterWorker | None = None
        self._sine_level_lock = threading.Lock()
        self._sine_level_dbfs: float | None = None

        self.sweep_gen = sweep_gen
        self.marker_gen = marker_gen
        self.alignment_engine = alignment_engine
        self.deconv_engine = deconv_engine
        self.harmonic_injector = harmonic_injector
        self.protection_filter = protection_filter
        self.verifier = DSPVerificationTool(self.hw['fs'])

        # Directories
        self.rec_dir = Path("./Recordings")
        self.dist_dir = self.rec_dir / "Distortion"
        self.debug_dir = None
        # The app starts a fresh session for a scan or standalone measurement.
        # Direct API users also have a default session until they set a directory.
        self._reference_session_id = uuid.uuid4().hex

        self._log_config()

    def _ensure_directories(self) -> None:
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        self.dist_dir.mkdir(parents=True, exist_ok=True)

        if self.cap['debug_saves']:
            self.debug_dir = self.rec_dir / "debug"
            self.debug_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.debug_dir = None

    def set_session_directory(self, session_path: Path) -> None:
        """Begin a new acquisition session in the supplied recording directory.

        Called once before a scan, or once per standalone measurement, even
        when two acquisitions use the same directory. Pausing a scan does not
        call this method, so its references remain shared after resuming.
        """
        self._reference_session_id = uuid.uuid4().hex
        self.rec_dir = Path(session_path)
        self.dist_dir = self.rec_dir / "Distortion"
        self._ensure_directories()
        logger.debug(f"Audio session directory updated to: {session_path}")

    def _save_shared_reference(self, arrays: Dict[str, np.ndarray],
                               acquisition: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """Save exact digital references once per session and return their link.

        Hash the samples, their names/shapes/dtypes and acquisition metadata. This means
        changed playback or inverse data cannot silently reuse an older file,
        while equal references from thousands of positions share one archive.
        """
        # A Unicode scalar is readable with allow_pickle=False. Never archive
        # Python objects or capture-specific alignment/processing results.
        metadata = dict(acquisition or {})
        metadata.update(schema="shared_reference_v2", sample_rate=self.hw['fs'])
        payload = dict(arrays)
        payload['metadata_json'] = np.array(json.dumps(
            metadata, sort_keys=True, separators=(',', ':'), allow_nan=False))
        digest = hashlib.sha256()
        digest.update(b'nfs_reference_v2')
        for name in sorted(payload):
            values = np.ascontiguousarray(payload[name])
            header = json.dumps([name, values.dtype.str, list(values.shape)],
                                separators=(',', ':')).encode('utf-8')
            # Length-prefix the header so different array descriptions cannot
            # accidentally produce the same concatenated input to the hash.
            digest.update(len(header).to_bytes(8, 'little'))
            digest.update(header)
            digest.update(values.tobytes())
        reference_id = digest.hexdigest()
        relative = Path('references') / self._reference_session_id / f'{reference_id}.npz'
        destination = self.debug_dir / relative
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(f'.{uuid.uuid4().hex}.tmp')
            try:
                # Compression is lossless: the float64 inverse stays exact.
                # Publish only a complete archive, never a half-written NPZ.
                with temporary.open('xb') as handle:
                    np.savez_compressed(handle, **payload)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        return {
            "reference_archive_schema": "shared_reference_v2",
            "reference_session_id": self._reference_session_id,
            "reference_id": reference_id,
            # Relative to the debug directory, so moving the project still works.
            "reference_file": relative.as_posix(),
        }

    def _log_config(self) -> None:
        """Log the current hardware and capture configuration."""
        logger.info(
            f"Audio Config: FS={self.hw['fs']}, Sweeps={self.cap['num_sweeps']}, Dur={self.cap['sweep_dur_s']}s")
        logger.info(f"Devices: In={self.hw['dev_in']}, Out={self.hw['dev_out']}")

    def _get_api_name(self, dev_index: int) -> str:
        """
        Get the name of the API used by the device at the given index.

        :param dev_index: Device index.
        :return: API name.
        """
        try:
            d = sd.query_devices(dev_index)
            return sd.query_hostapis()[d['hostapi']]['name'].upper()
        except:
            return "UNKNOWN"

    def _save_wav_with_metadata(self, filepath: Path, data: np.ndarray, title: str,
                                subtype: Optional[str] = None) -> None:
        """Saves a WAV file and embeds metadata into standard RIFF chunks."""
        channels = data.shape[1] if len(data.shape) > 1 else 1

        kwargs = {'mode': 'w', 'samplerate': self.hw['fs'], 'channels': channels}
        if subtype:
            kwargs['subtype'] = subtype

        with sf.SoundFile(str(filepath), **kwargs) as f:
            # f.title writes the INAM chunk
            f.title = title
            # f.comment writes the ICMT chunk; Windows is more likely to show this
            if hasattr(self.sweep_gen, "log_sweep_rate"):
                f.comment = (
                    f"{title}; ESS_inverse=matched_v1; "
                    f"ESS_L_s={self.sweep_gen.log_sweep_rate:.17g}; "
                    f"ESS_guard_samples={self.sweep_gen.guard_samples}; "
                    f"ESS_active_duration_s={self.sweep_gen.active_duration_s:.17g}; "
                    "capture_sequence=marker_then_dual_sweep_v1"
                )
            else:
                f.comment = title
            f.write(data)

    def _prepare_sweep(self) -> Dict[str, Any]:
        """Build marker, settling gap and simultaneous ESS for every repeat."""
        fs = self.hw['fs']
        # Generate the digital sweep and its inverse *once*. The same samples
        # are reused for every repeat, so the results can be averaged.
        s_fund, phase, inv_sweep = self.sweep_gen.generate()
        s_composite = s_fund.copy()
        if self.harmonic_injector:
            # Artificial H2/H3 goes only to the speaker. It must not enter
            # the inverse or the electrical reference signal.
            s_composite = self.harmonic_injector.inject(
                s_fund, phase, envelope=self.sweep_gen.fade_envelope,
                instantaneous_hz=self.sweep_gen.instantaneous_hz, fs=fs,
            )
        # dBFS is converted to a linear amplitude. Set the composite peak
        # (including test harmonics) to the requested safe playback level.
        playback_gain = DSPUtils.db_to_lin(self.cap['sweep_level_dbfs']) / (
            np.max(np.abs(s_composite)) + 1e-12
        )
        # Sounddevice streams float32 samples; the inverse stays float64 for
        # numerical precision during FFT deconvolution.
        s_play = (s_composite * playback_gain).astype(np.float32)
        # The electrical reference carries the same fundamental and gain. Test
        # harmonics and driver protection belong only to the speaker output.
        s_reference = (s_fund * playback_gain).astype(np.float32)
        if self.protection_filter:
            # The protection HPF is for the loudspeaker path only.
            s_play = self.protection_filter.apply(s_play).astype(np.float32)
        marker = self.marker_gen.generate()

        def milliseconds(key, default=None):
            # Capture settings use ms; array indices use whole samples.
            value = float(self.cap[key] if default is None else self.cap.get(key, default))
            if not np.isfinite(value) or value < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
            return int(round(value * fs / 1000))

        pre = milliseconds('pre_sil_ms')
        # The marker plays first on the reference output. Both outputs then
        # wait during the gap before their sweeps start together.
        gap = milliseconds('marker_gap_ms', 20.0)
        # This one silent interval both records room decay and separates
        # repeats. It is retained for processing, not followed by another wait.
        post = milliseconds('post_sil_ms')
        tail = post
        # Continue recording after the final slot so interface transport latency
        # does not truncate the final sweep. Alignment checks complete captures.
        flush = post
        # One slot is marker + gap + sweep + spacing after the sweep.
        offset = len(marker) + gap
        slot = offset + len(s_play) + post
        repeats = int(self.cap['num_sweeps'])
        if repeats < 1:
            raise ValueError("num_sweeps must be positive")
        total = pre + repeats * slot + flush
        # Zeros are intentional silence: the speaker is quiet for the marker;
        # both outputs are quiet in gaps and at the very end.
        tx_speaker = np.zeros(total, dtype=np.float32)
        tx_loop = np.zeros(total, dtype=np.float32)
        marker_starts = [pre + i * slot for i in range(repeats)]
        for start in marker_starts:
            # Only the electrical reference output gets the Barker marker.
            tx_loop[start:start + len(marker)] = marker
            sweep_start = start + offset
            # These two slices start at the same playback sample.
            tx_loop[sweep_start:sweep_start + len(s_reference)] = s_reference
            tx_speaker[sweep_start:sweep_start + len(s_play)] = s_play
        # Keep the signal arrays and timing plan together. The same timing
        # metadata later identifies exact slices in the raw recording.
        return {
            "tx_speaker": tx_speaker, "tx_loop": tx_loop, "marker": marker,
            "fundamental": s_fund, "speaker_sweep": s_play,
            "reference_sweep": s_reference, "inverse": inv_sweep,
            "timing": {
                "schema": "marker_then_dual_sweep_v1", "sample_rate": fs,
                "pre_samples": pre, "marker_samples": len(marker),
                "marker_gap_samples": gap, "sweep_offset_samples": offset,
                "sweep_samples": len(s_play), "capture_tail_samples": tail,
                "inverse_origin_sample": self.sweep_gen.inverse_origin_sample,
                "inverse_samples": len(inv_sweep), "inverse_method": "matched_full_fft_v2",
                "post_samples": post, "flush_samples": flush, "slot_samples": slot,
                "linear_ir_samples": max(len(s_play), tail + _LINEAR_IR_ORIGIN_SAMPLE + 1),
                "total_samples": total, "playback_marker_starts": marker_starts,
                "playback_gain": float(playback_gain),
            },
        }

    def _finish_sweep(self, prepared: Dict[str, Any], rec_mic: np.ndarray,
                      rec_loop: np.ndarray) -> Dict[str, Any]:
        """Align/average a completed capture and retain enough context for exact replay.

        The raw arrays remain untouched. Derived arrays contain the sweep and
        recorded decay, with only the microphone recording boundary faded.
        Hardware and mock acquisition both call this same processing stage.
        """
        # The recorded marker supplies timing. It does not replace the clean
        # digital sweep used to calculate the inverse filter.
        timing = dict(prepared['timing'])
        avg_mic, avg_loop, mic_slices, psr = self.alignment_engine.sync_and_average(
            rec_mic, rec_loop, prepared['marker'], timing['pre_samples'],
            timing['slot_samples'], timing['sweep_samples'],
            sweep_offset_samples=timing['sweep_offset_samples'],
            capture_tail_samples=timing['capture_tail_samples'],
        )
        # Store both planned and detected sample positions. They allow exact
        # offline replay, including any interface latency or repeat jitter.
        timing.update({
            "aligned_repeats": self.alignment_engine.last_alignment,
            "raw_capture_channels": ["electrical_loopback", "microphone"],
            "loopback_role": ("marker_alignment_and_linear_response_correction"
                              if self.cap.get('loopback_correction', True)
                              else "record_only; marker_used_for_alignment"),
            "inverse_reference": "clean_digital_fundamental",
            "loop_tail_conditioning": "none",
            "mic_tail_fade_ms": min(10.0, timing["capture_tail_samples"] * 1000.0 / self.hw["fs"]),
            "sweep_start_hz": self.sweep_gen.f1,
            "sweep_end_hz": self.hw['fs'] / 2,
            "active_duration_s": self.sweep_gen.active_duration_s,
            # L refers to the active exponential part, excluding the guards.
            "L_s": self.sweep_gen.log_sweep_rate,
            "guard_samples": self.sweep_gen.guard_samples,
            "hardware_config": dict(self.hw), "capture_config": dict(self.cap),
            "injected_harmonics_db": {
                "H2": self.harmonic_injector.h2_db if self.harmonic_injector else None,
                "H3": self.harmonic_injector.h3_db if self.harmonic_injector else None,
            },
            "speaker_protection_hpf": {
                key: getattr(self.protection_filter, key)
                for key in ('freq_hz', 'order', 'hpf_correction', 'hpf_corr_db_cap')
            } if self.protection_filter else None,
        })
        # A following marker must not contaminate the reference used for correction.
        # Use the earliest safe boundary across repeats, with a 2 ms marker margin.
        positions = timing['aligned_repeats']
        timing['loop_valid_samples'] = max(timing['sweep_samples'], min(
            [len(avg_loop)] + [b['marker_start_sample'] - a['sweep_start_sample'] - round(.002*self.hw['fs'])
                              for a, b in zip(positions, positions[1:])]))
        # Keep raw channels untouched, aligned averages separately, and the
        # original digital references at their actual array precision.
        return {
            "inv_sweep": prepared['inverse'],
            "tx_ref_signal": np.pad(prepared['reference_sweep'], (0, timing['capture_tail_samples'])),
            "rx_mic_conditioned": avg_mic, "rx_loop_aligned": avg_loop,
            "debug_mic_slices": mic_slices, "psr": psr,
            "psr_reference": self.alignment_engine.last_psr_reference,
            "acquisition_metadata": {
                "producer": "nfs.marker_then_dual_sweep_v1",
                "raw_capture_channels": ["electrical_loopback", "microphone"],
                "num_sweeps": self.cap['num_sweeps'],
                "layout": {key: value for key, value in prepared['timing'].items()
                           if key not in ('linear_ir_samples', 'actual_sample_rate',
                                          'stream_status')},
                "sweep": {
                    "start_hz": self.sweep_gen.f1, "end_hz": self.hw['fs'] / 2,
                    "active_duration_s": self.sweep_gen.active_duration_s,
                    "log_sweep_rate_s": self.sweep_gen.log_sweep_rate,
                    "guard_samples": self.sweep_gen.guard_samples,
                    "level_dbfs": self.cap['sweep_level_dbfs'],
                },
                "speaker_protection_hpf": {
                    "freq_hz": self.protection_filter.freq_hz,
                    "order": self.protection_filter.order,
                } if self.protection_filter else None,
                "injected_harmonics_db": timing['injected_harmonics_db'],
            },
            "capture_metadata": timing,
            "raw_recordings": (rec_loop, rec_mic),
            "digital_reference": {
                "fundamental": prepared['fundamental'], "inverse": prepared['inverse'],
                "speaker_sweep": prepared['speaker_sweep'],
                "loopback_sweep": prepared['reference_sweep'], "marker": prepared['marker'],
            },
        }

    def _run_sweep(self) -> Dict[str, Any]:
        """
        Executes the playback and recording of the composite signal.
        Handles stream synchronization, multiple sweep averaging, and loopback alignment.
        """
        if self.hw['ch_out_spkr'] == self.hw['ch_out_ref'] or self.hw['ch_in_mic'] == self.hw['ch_in_loop']:
            raise ValueError("Dual-channel capture requires distinct speaker/reference outputs and mic/loop inputs")
        prepared = self._prepare_sweep()
        tx_sweep_long = prepared['tx_speaker']
        tx_ref_long = prepared['tx_loop']
        total_len = len(tx_sweep_long)

        # Allocate one column per physical channel. Channel numbers in the
        # saved hardware settings are zero-based, even for ASIO devices.
        out_ch_count = max(self.hw['ch_out_spkr'], self.hw['ch_out_ref']) + 1

        out_frames = np.zeros((total_len, out_ch_count), dtype=np.float32)
        out_frames[:, self.hw['ch_out_spkr']] = tx_sweep_long
        out_frames[:, self.hw['ch_out_ref']] = tx_ref_long

        rec_loop = np.zeros(total_len, dtype=np.float32)
        rec_mic = np.zeros(total_len, dtype=np.float32)

        # ASIO uses channel selectors; other APIs use columns up to the
        # highest channel number. The callback below handles both layouts.
        out_api = self._get_api_name(self.hw['dev_out'])
        in_api = self._get_api_name(self.hw['dev_in'])
        use_asio_in, use_asio_out = ("ASIO" in in_api), ("ASIO" in out_api)

        # Use the same channel mapping policy for sweep and sine playback.
        in_args = _stream_channel_settings(
            in_api, (self.hw['ch_in_loop'], self.hw['ch_in_mic']), self.hw['wasapi_exclusive'])
        out_args = _stream_channel_settings(
            out_api, (self.hw['ch_out_spkr'], self.hw['ch_out_ref']), self.hw['wasapi_exclusive'])

        idx_play, idx_rec = 0, 0
        stream_status = []
        done_evt = threading.Event()
        meter_worker = _AudioMeterWorker(self.hw['fs'], self.hw['blocksize'])
        meter_worker.start()
        output_meter_roles = (0, 1) if use_asio_out else (
            self.hw['ch_out_spkr'],
            self.hw['ch_out_ref'],
        )
        input_meter_roles = (0, 1) if use_asio_in else (
            self.hw['ch_in_loop'],
            self.hw['ch_in_mic'],
        )

        # PortAudio calls this function with small blocks of live samples.
        # Keep it to copying buffers and simple bookkeeping; file saving and
        # FFT processing happen later, outside the real-time audio callback.
        def callback(indata, outdata, frames, time_info, status):
            nonlocal idx_play, idx_rec
            if status:
                stream_status.append({"sample": idx_rec, "status": str(status)})

            # Copy the next playback block. The two ASIO callback columns
            # correspond to the selected speaker/reference physical channels.
            n_out = min(frames, total_len - idx_play)
            if use_asio_out:
                outdata[:n_out, 0] = out_frames[idx_play:idx_play + n_out, self.hw['ch_out_spkr']]
                outdata[:n_out, 1] = out_frames[idx_play:idx_play + n_out, self.hw['ch_out_ref']]
                if frames > n_out:
                    outdata[n_out:] = 0
            else:
                outdata[:n_out, :out_args[0]] = out_frames[idx_play:idx_play + n_out, :out_args[0]]
                if frames > n_out:
                    outdata[n_out:] = 0

            # Record the reference and microphone in separate arrays. For
            # ASIO, input columns 0/1 correspond to the selected loop/mic.
            n_in = min(frames, total_len - idx_rec)
            if n_in > 0:
                if use_asio_in:
                    rec_loop[idx_rec:idx_rec + n_in] = indata[:n_in, 0]
                    rec_mic[idx_rec:idx_rec + n_in] = indata[:n_in, 1]
                else:
                    rec_loop[idx_rec:idx_rec + n_in] = indata[:n_in, self.hw['ch_in_loop']]
                    rec_mic[idx_rec:idx_rec + n_in] = indata[:n_in, self.hw['ch_in_mic']]

            meter_worker.submit(
                outdata,
                output_meter_roles,
                indata,
                input_meter_roles,
                status,
            )

            # Signal the waiting thread only when both directions are done.
            idx_play += n_out
            idx_rec += n_in
            if idx_play >= total_len and idx_rec >= total_len:
                done_evt.set()

        # ASIO needs COM on this calling thread for the stream's whole life.
        # The with blocks close the stream before releasing our COM reference.
        try:
            with _asio_thread_context(use_asio_in or use_asio_out), sd.Stream(device=(self.hw['dev_in'], self.hw['dev_out']), samplerate=self.hw['fs'],
                           blocksize=self.hw['blocksize'],
                           dtype="float32", channels=(in_args[0], out_args[0]), dither_off=True,
                           extra_settings=(in_args[1], out_args[1]), callback=callback) as stream:
                if not done_evt.wait(total_len / self.hw['fs'] + 10):
                    raise RuntimeError("Audio capture did not finish within the expected duration")
                # These are observed stream facts, useful when replaying or
                # diagnosing captures made on a different audio interface.
                prepared['timing']['actual_sample_rate'] = float(stream.samplerate)
                prepared['timing']['stream_latency_s'] = list(stream.latency)
                prepared['timing']['stream_status'] = stream_status
        finally:
            meter_worker.stop()
            reset_audio_meter_state(active=False)

        # Align and average after the stream has closed; the raw arrays remain
        # available for the optional debug archive.
        return self._finish_sweep(prepared, rec_mic, rec_loop)

    def measure_ir(self, position: CylindricalPosition, order_id: str = "NA", save: bool = True) -> dict[str, Any]:
        """
        Public entry point. Coordinates capture, processing, and file saving.
        """
        logger.info(f"Measuring IR at {position} (ID: {order_id})")
        if save:
            self._ensure_directories()

        # 1. Capture Raw Data (Run Sweeps)
        result = self._run_sweep()

        # 2. Filename formatting
        if self.cap.get('naming_convention') == 'tom':
            # tom's Format: (r, t, z).wav
            base_name = f"({position.r():.1f}, {position.t():.1f}, {position.z():.1f})"
            main_file_name = f"{base_name}.wav"
            dist_file_name = f"{base_name}_dist.wav"
        else:
            # dimitri's Format: ID_rX_phY_zZ_ir.wav
            base_name = (
                f"{order_id}_"
                f"r{DSPUtils.fmt_num_for_name(position.r())}_"
                f"ph{DSPUtils.fmt_num_for_name(position.t())}_"
                f"z{DSPUtils.fmt_num_for_name(position.z())}"
            )
            main_file_name = f"{base_name}_ir.wav"
            dist_file_name = f"{base_name}_ir_dist.wav"

        # 3. Deconvolve before returning metadata so it includes the actual correction.
        correction = {}
        if self.cap.get('loopback_correction', True):
            correction = dict(loop_data=result['rx_loop_aligned'],
                              reference_data=result['tx_ref_signal'],
                              log_sweep_rate=self.sweep_gen.log_sweep_rate,
                              loop_valid_samples=result['capture_metadata'].get('loop_valid_samples'))
        ir_full, ir_linear = self.deconv_engine.process_ir(
            result['rx_mic_conditioned'], result['inv_sweep'], self.protection_filter,
            inverse_origin_sample=result['capture_metadata'].get('inverse_origin_sample'),
            sweep_samples=result['capture_metadata']['sweep_samples'],
            linear_ir_samples=result['capture_metadata']['linear_ir_samples'], **correction)
        result['capture_metadata']['loopback_correction'] = dict(
            self.deconv_engine.last_loopback_correction)
        # Keep the archive field for older readers; the output HPF was removed.
        result['capture_metadata']['ir_highpass_hz'] = 0.0
        if correction:
            logger.info("Linear loopback correction applied (harmonics excluded; "
                        "700–1300 Hz gain preserved; marker alignment unchanged)")

        # 4. Optional debug artifacts: raw input and shared references.
        if save and self.cap['debug_saves']:
            logger.info("Saving raw capture and shared reference...")
            result['capture_metadata'].update(
                self._save_shared_reference(result['digital_reference'],
                                            result['acquisition_metadata'])
            )
            # Keep the marker and every unconditioned repeat from both channels.
            # Channel order is electrical loopback, then microphone.
            filename = f"{base_name}_capture.wav"
            self._save_wav_with_metadata(
                self.debug_dir / filename, np.column_stack(result['raw_recordings']),
                filename, subtype='FLOAT',
            )

        # 5. DSP Verification
        metrics = self.verifier.calculate_metrics(
            ir_full, ir_linear, result.get("psr", 0.0), result.get("psr_reference"),
            linear_start_sample=result['capture_metadata']['sweep_samples'] - (_LINEAR_IR_ORIGIN_SAMPLE + 1),
        )
        logger.info(
            f"DSP Metrics: SNR={metrics['snr_db']:.1f}dB, THD={metrics['thd_pct']:.2f}%, "
            f"marker clarity={metrics['psr_db']:.1f}dB / "
            f"{metrics.get('psr_reference_db', 0.0):.1f}dB reference "
            f"({metrics.get('alignment_score_10', 0.0):.1f}/10)")

        warnings = self.verifier.verify(metrics)
        for w in warnings:
            logger.warning(f"VERIFICATION FAILURE: {w}")

        if save:
            # 6. Save Final Files
            # Main (Linear)
            linear_path = self.rec_dir / main_file_name
            self._save_wav_with_metadata(linear_path, ir_linear, main_file_name, subtype='FLOAT')
            logger.info(f"Saved Linear IR: {linear_path.name}")

            # Secondary (Distortion)
            dist_path = self.dist_dir / dist_file_name
            self._save_wav_with_metadata(dist_path, ir_full, dist_file_name, subtype='FLOAT')
            logger.info(f"Saved Distortion IR: {dist_path.name}")

        # Save metrics to debug if enabled
        if save and self.cap['debug_saves']:
            try:
                with open(self.debug_dir / f"{base_name}_metrics.json", "w") as f:
                    json.dump(metrics, f, indent=4)
            except Exception as e:
                logger.warning(f"Failed to save metrics JSON: {e}")

        return {
            "name": main_file_name,
            "position": position,
            "fs": self.hw["fs"],
            "ir_linear": ir_linear,
            "ir_full": ir_full,
            "sweep_metadata": {
                "inverse": result['capture_metadata'].get('inverse_method', 'matched_v1'),
                "inverse_origin_sample": result['capture_metadata'].get(
                    'inverse_origin_sample', len(result['inv_sweep']) - 1),
                "loopback_correction": dict(self.deconv_engine.last_loopback_correction),
                "log_sweep_rate_s": self.sweep_gen.log_sweep_rate,
                "guard_samples": self.sweep_gen.guard_samples,
                "active_duration_s": self.sweep_gen.active_duration_s,
            },
            "metrics": metrics,
            "capture_metadata": result['capture_metadata'],
            "saved": save,
        }

    def play_sine(self, frequency: float, level_dbfs: float, duration_s: Optional[float] = 1.0) -> None:
        """
        Plays a sine wave at the specified frequency and level.

        Both finite and continuous tones bypass the driver protection filter;
        that filter belongs to sweep playback only.

        :param frequency: Frequency in Hz.
        :param level_dbfs: Level in dBFS.
        :param duration_s: Duration in seconds. If None, plays until stop_sine() is called.
        """
        self.stop_sine()
        self.update_sine_level(level_dbfs)

        logger.info(
            f"Playing sine: {frequency} Hz, {level_dbfs} dBFS, {'until stopped' if duration_s is None else f'for {duration_s} s'}")

        fs = self.hw['fs']
        target_amp = DSPUtils.db_to_lin(level_dbfs)

        out_dev = self.hw['dev_out']
        in_dev = self.hw['dev_in']
        out_api = self._get_api_name(out_dev)
        in_api = self._get_api_name(in_dev)
        use_asio_out = "ASIO" in out_api
        use_asio_in = "ASIO" in in_api

        in_args = _stream_channel_settings(
            in_api, (self.hw['ch_in_loop'], self.hw['ch_in_mic']), self.hw['wasapi_exclusive'])
        out_ch_count, out_args_extra = _stream_channel_settings(
            out_api, (self.hw['ch_out_spkr'], self.hw['ch_out_ref']), self.hw['wasapi_exclusive'])

        if duration_s is not None:
            n = int(round(duration_s * fs))
            t = np.arange(n) / fs
            sine = (np.sin(2 * np.pi * frequency * t) * target_amp).astype(np.float32)

            # Sine playback intentionally bypasses the sweep-only protection HPF.

            if use_asio_out:
                # ASIO mapping: we want to play sine on ch_out_spkr.
                # channel_selectors maps outdata[:, 0] to spkr, outdata[:, 1] to ref
                out_data = np.zeros((n, 2), dtype=np.float32)
                out_data[:, 0] = sine
                out_data[:, 1] = sine
            else:
                out_data = np.zeros((n, out_ch_count), dtype=np.float32)
                out_data[:, self.hw['ch_out_spkr']] = sine
                out_data[:, self.hw['ch_out_ref']] = sine

            idx_play = 0
            done_evt = threading.Event()
            meter_worker = _AudioMeterWorker(fs, self.hw['blocksize'])
            meter_worker.start()
            output_meter_roles = (0, 1) if use_asio_out else (
                self.hw['ch_out_spkr'],
                self.hw['ch_out_ref'],
            )
            input_meter_roles = (0, 1) if use_asio_in else (
                self.hw['ch_in_loop'],
                self.hw['ch_in_mic'],
            )

            def callback(indata, outdata, frames, time_info, status):
                nonlocal idx_play
                n_out = min(frames, n - idx_play)
                if n_out > 0:
                    outdata[:n_out, :out_ch_count] = out_data[idx_play:idx_play + n_out, :out_ch_count]
                if frames > n_out:
                    outdata[n_out:] = 0
                meter_worker.submit(
                    outdata,
                    output_meter_roles,
                    indata,
                    input_meter_roles,
                    status,
                )
                idx_play += n_out
                if idx_play >= n:
                    done_evt.set()

            try:
                with _asio_thread_context(use_asio_in or use_asio_out), sd.Stream(
                    device=(in_dev, out_dev),
                    samplerate=fs,
                    blocksize=self.hw['blocksize'],
                    dtype='float32',
                    channels=(in_args[0], out_ch_count),
                    dither_off=True,
                    extra_settings=(in_args[1], out_args_extra),
                    callback=callback,
                ):
                    done_evt.wait()
            finally:
                meter_worker.stop()
                reset_audio_meter_state(active=False)
        else:
            # Indefinite playback
            phase = 0.0
            phase_inc = 2 * np.pi * frequency / fs
            self._sine_meter_worker = _AudioMeterWorker(fs, self.hw['blocksize'])
            self._sine_meter_worker.start()
            output_meter_roles = (0, 1) if use_asio_out else (
                self.hw['ch_out_spkr'],
                self.hw['ch_out_ref'],
            )
            input_meter_roles = (0, 1) if use_asio_in else (
                self.hw['ch_in_loop'],
                self.hw['ch_in_mic'],
            )

            def callback(indata, outdata, frames, time_info, status):
                nonlocal phase
                t = np.arange(frames)
                with self._sine_level_lock:
                    level = level_dbfs if self._sine_level_dbfs is None else self._sine_level_dbfs
                s = (np.sin(phase + phase_inc * t) * DSPUtils.db_to_lin(level)).astype(np.float32)
                phase = (phase + phase_inc * frames) % (2 * np.pi)

                if use_asio_out:
                    outdata[:, 0] = s
                    outdata[:, 1] = s
                else:
                    outdata.fill(0)
                    outdata[:, self.hw['ch_out_spkr']] = s
                    outdata[:, self.hw['ch_out_ref']] = s
                self._sine_meter_worker.submit(
                    outdata,
                    output_meter_roles,
                    indata,
                    input_meter_roles,
                    status,
                )

            try:
                # Continuous playback outlives this method. Keep the COM
                # reference until stop_sine closes the stream on this thread.
                context = _asio_thread_context(use_asio_in or use_asio_out)
                context.__enter__()
                self._sine_com_context = context
                self._sine_owner_thread = threading.get_ident()
                self._sine_stream = sd.Stream(
                    device=(in_dev, out_dev),
                    samplerate=fs,
                    blocksize=self.hw['blocksize'],
                    channels=(in_args[0], out_ch_count),
                    extra_settings=(in_args[1], out_args_extra),
                    callback=callback,
                    dtype='float32',
                    dither_off=True,
                )
                self._sine_stream.start()
            except Exception:
                # Also close an allocated stream when start(), rather than
                # construction, fails; release COM only after closing it.
                self.stop_sine()
                raise

    def update_sine_level(self, level_dbfs: float) -> None:
        """Updates the level used by active continuous sine playback."""
        with self._sine_level_lock:
            self._sine_level_dbfs = float(level_dbfs)

    def stop_sine(self) -> None:
        """Stop continuous playback on the same thread that started it."""

        if (self._sine_com_context is not None
                and self._sine_owner_thread != threading.get_ident()):
            raise RuntimeError("Continuous audio must be stopped on its owning audio thread")
        if self._sine_stream is not None:
            logger.info("Stopping indefinite sine playback")
            try:
                try:
                    self._sine_stream.stop()
                finally:
                    self._sine_stream.close()
            except Exception as e:
                logger.debug(f"Error closing sine stream: {e}")
            self._sine_stream = None
        if self._sine_com_context is not None:
            context = self._sine_com_context
            self._sine_com_context = None
            self._sine_owner_thread = None
            context.__exit__(None, None, None)
        if self._sine_meter_worker is not None:
            self._sine_meter_worker.stop()
            self._sine_meter_worker = None
        with self._sine_level_lock:
            self._sine_level_dbfs = None
        # Explicit streams are already closed above. sounddevice.stop() only
        # handles play/rec convenience functions and would load an unused backend.
        reset_audio_meter_state(active=False)


class MockInterfaceAudio(Audio):
    """Digital Twin loopback simulating hardware latency and filters."""

    def _run_sweep(self) -> Dict[str, Any]:
        prepared = self._prepare_sweep()
        tx_sweep = prepared['tx_speaker']
        tx_ref = prepared['tx_loop']

        # 3. --- HARDWARE SIMULATION (The Loopback) ---
        fs = self.hw['fs']

        # A) CS4272 Simulation: 25-tap FIR filter
        # We use a stable windowed-sinc filter at ~21kHz. 
        # This safely rolls off near Nyquist and provides the exact 12-sample 
        # linear-phase group delay characteristic of the hardware.
        fir_taps = scipy.signal.firwin(25, 0.45 * fs, fs=fs)

        # A) CS4272 Stage 1 FIR: 25-tap Remez design for exact datasheet matching
        # Passband: 0 to 0.454*Nyquist, Stopband: 0.547*Nyquist to Nyquist
        #  nyq = fs / 2.0
        #  bands = [0, 0.454 * nyq, 0.547 * nyq, nyq]
        #  fir_taps = scipy.signal.remez(25, bands, [1, 0], fs=fs)

        # A) Identity Filter (Disables FIR effect)
        #fir_taps = np.array([1.0], dtype=np.float32)

        # B) 15Hz 1st-Order HPF (10k Ohm + 10uF RC circuit)
        # This simulates a typical AC coupling capacitor found in audio interfaces.
        hpf_sos = scipy.signal.butter(1, 15.0, btype='hp', fs=fs, output='sos')

        def apply_hardware_sim(sig: np.ndarray) -> np.ndarray:
            # 1. Add Padding: Append 100 samples of silence to the end 
            # This prevents the FIR filter from "slamming" into the end of the array
            padding_len = 100
            sig_padded = np.concatenate([sig, np.zeros(padding_len)])

            # 2. Apply Digital FIR (Linear Phase, 12-sample group delay)
            # Now the "ringing" has room to decay into the padding
            y_padded = scipy.signal.lfilter(fir_taps, 1.0, sig_padded)

            # 3. Trim: Remove the padding to return to original length
            y = y_padded[:len(sig)]

            # 4. Apply Analog HPF (Minimum Phase)
            y = scipy.signal.sosfilt(hpf_sos, y)

            # 5. Apply 20ms Latency (Linear shift)
            delay_samps = int(0.020 * fs)
            y = np.concatenate([np.zeros(delay_samps), y[:-delay_samps]])

            # 6. Add Noise Floor (-100dBFS)
            noise = np.random.normal(0, 1e-5, len(y))
            return (y + noise).astype(np.float32)

        logger.info("► Loopback Mode: Applying CS4272 FIR, 15Hz HPF, and 20ms delay.")
        rec_mic = apply_hardware_sim(tx_sweep)
        rec_loop = apply_hardware_sim(tx_ref)

        return self._finish_sweep(prepared, rec_mic, rec_loop)

    def play_sine(self, frequency: float, level_dbfs: float, duration_s: Optional[float] = 1.0) -> None:
        """
        Plays a sine wave at the specified frequency and level.

        Both finite and continuous tones bypass the driver protection filter;
        that filter belongs to sweep playback only.

        :param frequency: Frequency in Hz.
        :param level_dbfs: Level in dBFS.
        :param duration_s: Duration in seconds. If None, plays until stop_sine() is called.
        """
        logger.info(
            f"[MOCK-INTERFACE] Playing sine: {frequency} Hz, {level_dbfs} dBFS, {'until stopped' if duration_s is None else f'for {duration_s} s'}")
        self.update_sine_level(level_dbfs)

    def update_sine_level(self, level_dbfs: float) -> None:
        logger.info(f"[MOCK-INTERFACE] Updated sine level: {level_dbfs} dBFS")

    def stop_sine(self) -> None:
        """Stops sine playback."""
        logger.info("[MOCK-INTERFACE] Stopped sine playback")
        reset_audio_meter_state(active=False)


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY
# ─────────────────────────────────────────────────────────────────────────────

class AudioMock(IAudio):
    """Simulation class for when hardware is unavailable."""

    def measure_ir(self, position: CylindricalPosition, order_id: str = "NA", save: bool = True) -> dict[str, Any]:
        """
        Measure the impulse response at a given position.

        :param position: The scanner position.
        :param order_id: An optional order ID for file naming.
        """
        logger.debug(f"[MOCK] Measured {position}, ID={order_id}")
        # time.sleep(1.0)  # Simulate sweep duration
        return {}

    def play_sine(self, frequency: float, level_dbfs: float, duration_s: Optional[float] = 1.0) -> None:
        """
        Plays a sine wave at the specified frequency and level.

        :param frequency: Frequency in Hz.
        :param level_dbfs: Level in dBFS.
        :param duration_s: Duration in seconds. If None, plays until stop_sine() is called.
        """
        logger.debug(
            f"[MOCK] Playing sine: {frequency} Hz, {level_dbfs} dBFS, {'until stopped' if duration_s is None else f'for {duration_s} s'}")
        self.update_sine_level(level_dbfs)

    def update_sine_level(self, level_dbfs: float) -> None:
        logger.debug(f"[MOCK] Updated sine level: {level_dbfs} dBFS")

    def stop_sine(self) -> None:
        """Stops sine playback."""
        logger.debug("[MOCK] Stopped sine playback")
        reset_audio_meter_state(active=False)


class AudioFactory:
    """Parses config and performs Dependency Injection assembling."""

    @staticmethod
    def _normalize_decimal_text(value: str) -> str:
        return value.replace(",", ".")

    @staticmethod
    def _get_required_config(config: configparser.ConfigParser, section: str, key: str, type_func: Any) -> Any:
        if not config.has_option(section, key):
            raise KeyError(f"Missing required config: [{section}] {key}")
        val = config.get(section, key).split('#')[0].split(';')[0].strip()
        if type_func is bool:
            # bool("False") is True in Python: nonempty text is truthy.
            # Decode the configuration value so disabled options stay off.
            value = val.casefold()
            if value not in configparser.ConfigParser.BOOLEAN_STATES:
                raise ValueError(f"Not a boolean: [{section}] {key} = {val}")
            return configparser.ConfigParser.BOOLEAN_STATES[value]
        if type_func in (float, int):
            val = AudioFactory._normalize_decimal_text(val)
        return type_func(val)

    @staticmethod
    def create(config_file: str, audio_section: str = 'audio') -> IAudio:
        """
        Creates Audio instance based on 'mode' config (hardware, loopback, mock).
        """
        config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
        config.read(config_file)

        if not config.has_section(audio_section):
            raise KeyError(f"Config file missing [{audio_section}] section")

        # Determine operating mode
        mode = config.get(audio_section, 'mode', fallback='hardware').lower()

        if mode == 'mock':
            return AudioMock()

        # Check registry first for non-standard modes
        # Built-in modes should skip this
        if mode not in ['mock', 'hardware', 'loopback', 'mock_interface']:
            try:
                component = registry.audio.get(mode)
                return component(config_file, audio_section)
            except ValueError:
                pass  # Fallback

        sweep_section = 'sweep'
        if not config.has_section(sweep_section):
            raise KeyError(f"Config file missing [{sweep_section}] section")

        def parse_optional_float(s):
            return None if s.lower() == "none" else float(AudioFactory._normalize_decimal_text(s))

        def optional_config_int(key: str, fallback: int = -1) -> int:
            raw = config.get(audio_section, key, fallback=str(fallback)).split('#')[0].split(';')[0].strip()
            try:
                return int(float(AudioFactory._normalize_decimal_text(raw)))
            except ValueError:
                return fallback

        fs = AudioFactory._get_required_config(config, audio_section, 'fs', int)
        sweep_dur_s = AudioFactory._get_required_config(config, sweep_section, 'sweep_dur_s', float)
        sweep_level_dbfs = AudioFactory._get_required_config(config, sweep_section, 'sweep_level_dbfs', float)

        configured_in = optional_config_int('in_dev')
        configured_out = optional_config_int('out_dev')

        saved_in_name = config.get(audio_section, 'in_dev_name', fallback='').strip()
        saved_in_api = config.get(audio_section, 'in_dev_hostapi', fallback='').strip()
        saved_out_name = config.get(audio_section, 'out_dev_name', fallback='').strip()
        saved_out_api = config.get(audio_section, 'out_dev_hostapi', fallback='').strip()

        if mode == 'mock_interface':
            dev_in = configured_in
            dev_out = configured_out
        else:
            dev_in = resolve_device_id_by_saved_identity(
                saved_in_name,
                saved_in_api,
                role_label="input",
                require_input=True,
            )
            dev_out = resolve_device_id_by_saved_identity(
                saved_out_name,
                saved_out_api,
                role_label="output",
                require_output=True,
            )
        if configured_in >= 0 and dev_in != configured_in:
            logger.info(
                f"Input device '{saved_in_name}' is saved as ID {configured_in}; resolved current ID {dev_in}."
            )
        if configured_out >= 0 and dev_out != configured_out:
            logger.info(
                f"Output device '{saved_out_name}' is saved as ID {configured_out}; resolved current ID {dev_out}."
            )

        hw_config = {
            'dev_in': dev_in,
            'dev_out': dev_out,
            'ch_in_mic': AudioFactory._get_required_config(config, audio_section, 'in_ch_mic', int),
            'ch_in_loop': AudioFactory._get_required_config(config, audio_section, 'in_ch_loop', int),
            'ch_out_spkr': AudioFactory._get_required_config(config, audio_section, 'out_ch_spkr', int),
            'ch_out_ref': AudioFactory._get_required_config(config, audio_section, 'out_ch_ref', int),
            'fs': fs,
            'blocksize': AudioFactory._get_required_config(config, audio_section, 'blocksize', int),
            'wasapi_exclusive': AudioFactory._get_required_config(config, audio_section, 'wasapi_exclusive', bool),
        }

        cap_config = {
            'loopback_correction': config.getboolean(sweep_section, 'loopback_correction', fallback=True),
            'naming_convention': config.get(sweep_section, 'naming_convention', fallback='dimitri').strip(),
            'debug_saves': AudioFactory._get_required_config(config, sweep_section, 'debug_saves', bool),
            'sweep_dur_s': sweep_dur_s,
            'sweep_level_dbfs': sweep_level_dbfs,
            'num_sweeps': AudioFactory._get_required_config(config, sweep_section, 'num_sweeps', int),
            'pre_sil_ms': AudioFactory._get_required_config(config, sweep_section, 'pre_sil_ms', float),
            'post_sil_ms': AudioFactory._get_required_config(config, sweep_section, 'post_sil_ms', float),
            'marker_gap_ms': 20.0,  # Fixed marker settling gap; adjust here if needed.
        }

        # Initialize core components
        sweep_gen = SweepGenerator(fs, sweep_dur_s, f1=1.0, level_dbfs=sweep_level_dbfs)
        marker_gen = MarkerGenerator(fs, 100.0, (500.0, 5000.0), sweep_level_dbfs)

        alignment_engine = AlignmentEngine(
            fs,
            cap_config['num_sweeps'],
            AudioFactory._get_required_config(config, sweep_section, 'align_to_first_marker', bool),
            AudioFactory._get_required_config(config, sweep_section, 'mic_tail_taper_ms', float),
            marker_gen.dur_ms
        )

        deconv_engine = DeconvolutionEngine(fs)

        # Gatekeeper logic for optional pipeline stages
        h2_db = AudioFactory._get_required_config(config, sweep_section, 'H2_TEST_DB', parse_optional_float)
        h3_db = AudioFactory._get_required_config(config, sweep_section, 'H3_TEST_DB', parse_optional_float)
        injector = HarmonicInjector(h2_db, h3_db) if (h2_db is not None or h3_db is not None) else None

        protect_hz = AudioFactory._get_required_config(config, sweep_section, 'PROTECT_HPF_HZ', parse_optional_float)
        if protect_hz is not None and protect_hz > 0:
            order = AudioFactory._get_required_config(config, sweep_section, 'PROTECT_HPF_ORDER', int)
            hpf_correction = config.getboolean(sweep_section, 'PROTECT_HPF_CORRECTION', fallback=False)
            hpf_corr_raw = config.get(sweep_section, 'PROTECT_HPF_CORR_DB_CAP', fallback='12.0')
            hpf_corr_raw = hpf_corr_raw.split('#')[0].split(';')[0].strip()
            hpf_corr_db_cap = float(AudioFactory._normalize_decimal_text(hpf_corr_raw))
            filter_engine = ProtectionFilter(fs, protect_hz, order, hpf_correction, hpf_corr_db_cap)
        else:
            filter_engine = None

        kwargs = {
            'hw_config': hw_config,
            'capture_config': cap_config,
            'sweep_gen': sweep_gen,
            'marker_gen': marker_gen,
            'alignment_engine': alignment_engine,
            'deconv_engine': deconv_engine,
            'harmonic_injector': injector,
            'protection_filter': filter_engine
        }

        # Route to the correct class based on mode
        if mode == 'mock_interface':
            return MockInterfaceAudio(**kwargs)

        return Audio(**kwargs)


if __name__ == "__main__":
    # Helper to list devices if run directly
    print("\nAvailable Audio Devices:")
    print(sd.query_devices())
