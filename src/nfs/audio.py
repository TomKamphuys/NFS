"""
Audio Measurement Engine
========================

This module implements a robust acoustic measurement engine using Exponential
Sine Sweeps (ESS). It is designed for high-precision Impulse Response (IR)
acquisition with driver protection and distortion analysis capabilities.

Key Features:
    * Weighted Farina Method: Uses Exponential Sine Sweeps (ESS) to separate 
      linear impulse response from harmonic distortion products. Distortion 
      orders appear at negative time offsets relative to the main impulse.
    * Time Reversal Deconvolution: The inverse filter is generated via 
      time-reversal of the excitation signal (with amplitude envelope 
      correction), ensuring maximal SNR out of band.
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
      Total Harmonic Distortion (THD), and Peak Sharpness Ratio (PSR) for alignment quality.
    * Mock Interface: A digital twin loopback mode that simulates hardware latency 
      and filter effects, allowing testing without physical audio devices.
"""

import configparser
import os
import threading
import time
from abc import ABC, abstractmethod
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

# Enable ASIO build of PortAudio in python-sounddevice (Windows).
# This environment variable triggers the loading of ASIO drivers if available.
os.environ["SD_ENABLE_ASIO"] = "1"
import sounddevice as sd  # noqa: E402


_METER_EPS = 1e-12
_METER_STALE_TIMEOUT_S = 1.5
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
    ASIO uses 0-based channel indices; others use 1-based channel indices.
    """
    # Hard refresh PortAudio state to catch any recent device changes
    sd._terminate()
    sd._initialize()

    apis = sd.query_hostapis()
    devs = sd.query_devices()

    device_catalog = {}

    for api_idx, a in enumerate(apis):
        api_name = a['name']
        is_asio = "ASIO" in api_name.upper()
        base_idx = 0 if is_asio else 1

        for dev_idx, d in enumerate(devs):
            if d['hostapi'] == api_idx:
                max_in = d['max_input_channels']
                max_out = d['max_output_channels']

                in_ch_indices = list(range(base_idx, max_in + base_idx)) if max_in > 0 else []
                out_ch_indices = list(range(base_idx, max_out + base_idx)) if max_out > 0 else []

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
    common_rates = [44100, 48000, 88200, 96000, 176400, 192000]
    supported = []

    for rate in common_rates:
        try:
            if input_device_id is not None:
                sd.check_input_settings(device=input_device_id, samplerate=rate)
            if output_device_id is not None:
                sd.check_output_settings(device=output_device_id, samplerate=rate)
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

    return fallback_match


class DSPVerificationTool:
    """
    Calculates Quality Metrics from Impulse Responses and Alignment data.
    Provides automated verification of SNR, THD, and Timing Jitter.
    """

    def __init__(self, fs: int):
        """
        Initialize the DSP verification tool.

        :param fs: The sample rate (Hz).
        """
        self.fs = fs

    def calculate_metrics(self, h_full: np.ndarray, h_linear: np.ndarray, psr: float) -> Dict[str, float]:
        """
        Calculates a suite of DSP quality metrics.
        
        :param h_full: Full IR including distortion products at negative time.
        :param h_linear: Cropped linear IR.
        :param psr: Peak Sharpness Ratio from the alignment engine.
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
        # h_linear starts at split_idx. Anything before split_idx - 100 samples is distortion.
        split_idx = len(h_full) // 2  # Approximate based on DeconvolutionEngine logic
        distortion_part = h_full[:split_idx - 50]

        linear_energy = np.sum(h_linear ** 2)
        distortion_energy = np.sum(distortion_part ** 2)

        metrics['thd_pct'] = float(np.sqrt(distortion_energy / (linear_energy + 1e-12)) * 100.0)

        # 3. Alignment Quality
        metrics['psr'] = float(psr)

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
            warnings.append(f"POOR ALIGNMENT: PSR is {metrics['psr']:.1f}. Possible clock drift or phase smear.")

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

    def __init__(self, fs: int, dur_ms: float, bw_hz: Tuple[float, float], level_dbfs: float):
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


class SweepGenerator:
    """
    Generates an Exponential Sine Sweep (ESS) and its CLEAN inverse filter.
    
    Outputs:
      1. The fundamental sweep signal.
      2. The phase array (useful for optional harmonic injection downstream).
      3. A 'clean' Inverse Filter using Time Reversal of the fundamental.
    """

    def __init__(self, fs: int, duration_s: float, f1: float, level_dbfs: float):
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
        """Generates fundamental sweep, phase array, and time-reversed inverse filter for harmonic injection"""
        f2 = self.fs * 0.5  # Nyquist frequency (half the sample rate)
        n = int(round(self.T * self.fs))  # Total number of samples in the sweep
        t = np.arange(n) / self.fs  # Time array from 0 to T seconds
        w1, w2 = 2 * np.pi * self.f1, 2 * np.pi * f2  # Angular frequencies for start and end
        L = self.T / np.log(w2 / w1)  # Logarithmic sweep rate constant

        # 1. Fundamental (Clean) - Used for generating the Inverse Filter
        # The phase equation for an exponential sweep ensures a pink noise spectrum (-3dB/octave).
        phase = w1 * L * (np.exp(t / L) - 1.0)
        s_fund = np.sin(phase).astype(np.float64)

        # 2. Generate CLEAN Inverse (Using Time Reversal)
        # We use the clean fundamental for the inverse to avoid "baking in" the distortion 
        # or protection filter into the reference.
        envelope = np.exp(-t / L)  # Amplitude envelope to correct pink spectrum to white (flat frequency response)
        inv = s_fund[::-1] * envelope  # Time Reversal effectively conjugates the phase in frequency domain

        # Normalize Inverse in Frequency Domain to ensure unity gain convolution
        Nfft = int(2 ** np.ceil(np.log2(len(s_fund) + len(inv) - 1)))
        S_fft = np.fft.rfft(s_fund, n=Nfft)
        I_fft = np.fft.rfft(inv, n=Nfft)
        peak_val = np.max(np.abs(np.fft.irfft(S_fft * I_fft, n=Nfft)))

        inv /= (peak_val + 1e-15)
        return s_fund, phase, inv


class HarmonicInjector:
    """Injects H2/H3 distortions using the sweep's phase array."""

    def __init__(self, h2_db: Optional[float] = None, h3_db: Optional[float] = None):
        """
        Initialize the harmonic injector.

        :param h2_db: Level of H2 in dB relative to fundamental.
        :param h3_db: Level of H3 in dB relative to fundamental.
        """
        self.h2_db = h2_db
        self.h3_db = h3_db

    def inject(self, s_fund: np.ndarray, phase: np.ndarray) -> np.ndarray:
        s_composite = s_fund.copy()

        # --- HARMONIC INJECTION (TESTING) ---
        # Adds artificial distortion to verify that the Farina separation logic works.
        if self.h2_db is not None:
            amp_h2 = DSPUtils.db_to_lin(self.h2_db)
            logger.info(f"► INJECTING H2 @ {self.h2_db} dB")
            s_composite += amp_h2 * np.sin(2 * phase)

        if self.h3_db is not None:
            amp_h3 = DSPUtils.db_to_lin(self.h3_db)
            logger.info(f"► INJECTING H3 @ {self.h3_db} dB")
            s_composite += amp_h3 * np.sin(3 * phase)

        return s_composite


class ProtectionFilter:
    """Applies MIN phase HPF to protect drivers, and generates an inverse correction mask."""

    def __init__(self, fs: int, freq_hz: float, order: int, hpf_correction: bool = False, hpf_corr_db_cap: float = 12.0):
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

        logger.info(f"► Applying Protection HPF: {self.freq_hz}Hz, Order={self.order} (Min Phase)")
        return scipy.signal.sosfilt(self.sos, sig).astype(np.float32)

    def get_correction_mask(self, n_bins: int) -> np.ndarray:
        """
        Generates the driver protection HPF frequency-domain correction mask for the deconvolution engine.
        Hard-caps the magnitude gain.
        """
        if self.sos is None or not self.hpf_correction:
            return np.ones(n_bins, dtype=np.complex64)

        logger.info(f"► Generating HPF Correction Mask (Max Gain: +{self.hpf_corr_db_cap}dB)")

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

    def _matched_filter_detect(self, x: np.ndarray, ref: np.ndarray, search_start: int = None,
                               search_end: int = None) -> Tuple[int, float, float]:
        """
        Finds the best match of signal 'ref' within 'x' using a matched filter.
        Returns the lag (index), the correlation coefficient, and the PSR.
        """
        lags, corr = DSPUtils.rfft_xcorr(x, ref)
        if search_start is None:
            search_start = 0
        if search_end is None:
            search_end = len(x) - 1

        m = (lags >= search_start) & (lags <= search_end)
        lags_sel, corr_sel = lags[m], corr[m]

        if len(lags_sel) == 0:
            return 0, 0.0, 0.0
        i = int(np.argmax(corr_sel))
        peak_val = float(corr_sel[i])

        # --- BEGIN QUALITY / PSR CHECK ---
        # Barker-13 autocorrelation main lobe is 2 chips wide. 
        # We dynamically calculate this width to mask only the main peak.
        chip_dur_s = (self.marker_dur_ms / 1000.0) / 13.0
        exclusion_samps = int((chip_dur_s * 2.0) * self.fs)
        mask_start = max(0, i - exclusion_samps)
        mask_end = min(len(corr_sel), i + exclusion_samps)

        corr_masked = corr_sel.copy()
        corr_masked[mask_start:mask_end] = 0.0  # Zero out the main lobe

        sidelobe_val = float(np.max(corr_masked))
        psr = peak_val / (sidelobe_val + 1e-12) if sidelobe_val > 0 else 99.0

        # If the peak is less than 2.5x the height of the sidelobes, alignment is dangerously smeared.
        if psr < 2.5:
            logger.warning(f"POOR MARKER ALIGNMENT: Correlation Peak Sharpness is {psr:.1f}. Phase smearing detected.")
        # --- END QUALITY / PSR CHECK ---

        # --- BEGIN DEBUG DATA SAVE BLOCK ---
        try:
            debug_dir = Path("./Recordings/debug")
            if debug_dir.exists():
                norm_x = np.linalg.norm(x[max(0, lags_sel[i]):max(0, lags_sel[i]) + len(ref)])
                norm_ref = np.linalg.norm(ref)
                match_pct = float(corr_sel[i]) / (norm_x * norm_ref + 1e-12) if (norm_x * norm_ref) > 0 else 0.0

                np.savez(
                    debug_dir / "alignment_debug.npz",
                    x=x,
                    ref=ref,
                    lags=lags,
                    corr=corr,
                    peak_idx=int(lags_sel[i]),
                    match_pct=match_pct,
                    psr=psr
                )
        except Exception as e:
            logger.warning(f"Failed to save alignment debug data: {e}")
        # --- END DEBUG DATA SAVE BLOCK ---

        return int(lags_sel[i]), peak_val, psr

    def sync_and_average(self, rec_mic: np.ndarray, rec_loop: np.ndarray, marker_single: np.ndarray,
                         pre_samps_settle: int, slot_len: int, sweep_len: int) -> Tuple[np.ndarray, np.ndarray, List[np.ndarray], float]:
        """
        Align multiple sweeps and average them to increase SNR.

        :param rec_mic: Recorded microphone signal.
        :param rec_loop: Recorded loopback signal.
        :param marker_single: The reference marker signal.
        :param pre_samps_settle: Number of samples to skip at the beginning.
        :param slot_len: Total length of one sweep slot (including silence/markers).
        :param sweep_len: Length of the actual sweep signal.
        :return: Tuple of (averaged mic, averaged loop, individual mic slices, PSR).
        """

        # --- Alignment & Averaging ---
        mic_slices = []
        loop_slices = []
        capture_len = sweep_len + int(round(self.mic_tail_taper_ms / 1000.0 * self.fs))

        # --- FIXED ALIGNMENT LOGIC ---
        # 1. Find a global anchor (first marker) using Matched Filter
        search_limit = pre_samps_settle + slot_len
        k_first_marker, _, psr = self._matched_filter_detect(rec_loop, marker_single, search_end=search_limit)

        # The correlation peak IS the start.
        t0_first_sweep = k_first_marker
        logger.debug(f"Marker found at {k_first_marker}. Using this as T0. PSR={psr:.1f}")

        window_samps = int(0.005 * self.fs)  # 5ms search window for re-sync

        for i in range(self.num_sweeps):
            expected_t0 = t0_first_sweep + (i * slot_len)

            if self.align_to_first_marker:
                # Sample-based cut: Rely on the first marker and constant sample rate
                start_idx = expected_t0
            else:
                # Per-sweep alignment: Re-sync to the marker for *every* sweep
                # Corrects for minor clock drift in very long sequences
                s_start = max(0, expected_t0 - window_samps)
                s_end = min(len(rec_loop), expected_t0 + window_samps)
                k_local, _, psr_local = self._matched_filter_detect(rec_loop, marker_single, search_start=s_start,
                                                                    search_end=s_end)
                start_idx = k_local
                if i == 0:
                    psr = psr_local  # Use first sweep PSR as representative if per-sweep

            end_idx = start_idx + capture_len
            # Stores aligned capture window copies into accumulation lists
            if end_idx <= len(rec_mic) and start_idx >= 0:
                mic_slices.append(rec_mic[start_idx: end_idx].copy())
                loop_slices.append(rec_loop[start_idx: end_idx].copy())

        if not mic_slices:
            raise RuntimeError("No valid sweeps captured (Alignment failed).")

        # Synchronous Averaging to lower the noise floor
        # By averaging multiple sweeps, the correlated signal (the sweep) adds coherently,
        # while the uncorrelated noise (background noise) adds incoherently, improving SNR.
        avg_mic = np.mean(mic_slices, axis=0)
        avg_loop = np.mean(loop_slices, axis=0)

        # Fade out tail using the unified _hann_fade (approx 10ms)
        avg_mic = DSPUtils.hann_fade(avg_mic, 10.0, self.fs, side="out")

        return avg_mic.astype(np.float32), avg_loop.astype(np.float32), mic_slices, psr


class DeconvolutionEngine:
    """Handles FFT deconvolution, spectral masking, and Farina separation."""

    def __init__(self, fs: int):
        """
        Initialize the deconvolution engine.

        :param fs: The sample rate (Hz).
        """
        self.fs = fs

    def process_ir(self, mic_data: np.ndarray, inv_data: np.ndarray, protection_filter: Optional[ProtectionFilter] = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Performs deconvolution to extract the Impulse Response.
        
        Implements:
          1. Frequency Domain Deconvolution (Y / X).
          2. Spectral Masking (Butterworth DC protection & HF taming).
          3. Minimum Phase Reconstruction (Cepstrum method) for the filter mask.
          4. Weighted Farina Separation: Splitting the linear IR from the 
             distortion products which appear at negative time.
             
        Returns:
            ir_full:   The full time-domain result containing linear IR + distortion echoes.
            ir_linear: The cropped linear response (causal part).
        """
        n_conv = len(mic_data) + len(inv_data) - 1
        Nfft = int(2 ** np.ceil(np.log2(n_conv)))

        # Go to Frequency Domain
        # rfft (real FFT) is used since our time-domain signals are purely real, 
        # which is faster and saves memory compared to a full complex FFT.
        Y = np.fft.rfft(mic_data, n=Nfft)
        I = np.fft.rfft(inv_data, n=Nfft)

        # --- Apply HPF Correction (If active) ---
        if protection_filter:
            hpf_mask = protection_filter.get_correction_mask(len(I))
            I = I * hpf_mask

        # --- Spectral Mask Generation ---
        freqs = np.fft.rfftfreq(Nfft, d=1.0 / self.fs)  # Frequency array for the positive half of the spectrum

        # LF Mask - Standard Butterworth @ 5Hz (Fixed per requirement)
        safe_freqs = np.maximum(freqs, 1e-9)
        lf_mask = 1.0 / np.sqrt(1.0 + (5.0 / safe_freqs) ** 2)
        lf_mask[0] = 0.0

        # HF Mask (Taper near Nyquist to avoid aliasing)
        hf_mask = np.ones_like(freqs)
        f_hf_start = 20000 if self.fs <= 48000 else 21000
        idx_hf_start = np.searchsorted(freqs, f_hf_start)

        # Set the taper to end exactly one bin before Nyquist.
        # len(hf_mask) - 1 is the actual Nyquist bin index.
        idx_hf_end = len(hf_mask) - 1 

        # Tapers high-frequency mask with a cosine window to suppress ringing
        if idx_hf_end > idx_hf_start:
            # np.linspace here ensures the last element of the slice (idx_hf_end - 1) 
            # reaches exactly 0.0 (where cos(pi) = -1).
            n = np.linspace(0, 1, idx_hf_end - idx_hf_start)
            hf_mask[idx_hf_start: idx_hf_end] = 0.5 * (1 + np.cos(np.pi * n))

        # Explicitly zero out the Nyquist bin to be sure
        if idx_hf_end < len(hf_mask):
            hf_mask[idx_hf_end:] = 0.0
        hf_mask[-1] = 0.0 

        mag_mask = lf_mask * hf_mask
        
                
        # Minimum Phase Complex Mask Generation (Cepstrum Method)
        # This creates a filter kernel that has the magnitude of 'mag_mask' but
        # minimum phase characteristics (energy concentrated at the start).
        # We compute the real cepstrum (inverse FFT of the log magnitude), apply a lifter 
        # window to select the causal part, and transform back to frequency domain.
        mag_spec = np.maximum(mag_mask, 1e-12)
        log_mag = np.log(mag_spec)
        if Nfft % 2 == 0:
            log_mag_full = np.concatenate([log_mag, log_mag[-2:0:-1]])
        else:
            log_mag_full = np.concatenate([log_mag, log_mag[-1:0:-1]])

        cepstrum = np.fft.ifft(log_mag_full).real
        w = np.zeros(Nfft)
        w[0] = 1.0
        mid = Nfft // 2
        if Nfft % 2 == 0:
            w[mid] = 1.0
            w[1:mid] = 2.0
        else:
            w[1:mid + 1] = 2.0

        H_min_phase = np.exp(np.fft.fft(cepstrum * w))[:len(mag_spec)]

        # Deconvolve & Apply Mask
        I_filtered = I * H_min_phase
        H_complex = Y * I_filtered
        h_full = np.fft.irfft(H_complex, n=Nfft).astype(np.float32)

        # --- WINDOWING & SEPARATION of Linear and Distortion IRs ---

        # Truncate to remove ghost IR from length > sweep duration (inv_data) *2 
        h_full = h_full[:len(inv_data) * 2]

        # Calculate fade: 10% of one sweep in ms
        fade_ms = (len(inv_data) / self.fs) * 100.0
        # Send to hann_fade util
        h_full = DSPUtils.hann_fade(h_full, fade_ms, self.fs, side="out")

        # Slice the Linear IR from the full IR
        # 50 is the number of samples before the exact middle split point between distortion and linear IR to avoid
        # the peak of the linear IR being pushed exactly to the fist sample if there is zero acoustic delay.
        split_idx = len(inv_data) - 50  # -50 samples split point.
        h_linear = h_full[split_idx: split_idx + len(inv_data)]

        return h_full, h_linear


# ─────────────────────────────────────────────────────────────────────────────
#  INTERFACES
# ─────────────────────────────────────────────────────────────────────────────

class IAudio(ABC):
    @abstractmethod
    def measure_ir(self, position: CylindricalPosition, order_id: str = "NA", save: bool = True):
        pass

    @abstractmethod
    def play_sine(self, frequency: float, level_dbfs: float, duration_s: Optional[float] = 1.0) -> None:
        """
        Plays a sine wave at the specified frequency and level.

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

        self._log_config()

    def _ensure_directories(self):
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        self.dist_dir.mkdir(parents=True, exist_ok=True)

        if self.cap['debug_saves']:
            self.debug_dir = self.rec_dir / "debug"
            self.debug_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.debug_dir = None

    def set_session_directory(self, session_path: Path):
        """Updates internal recording and debug directories to a measurement session path."""
        self.rec_dir = Path(session_path)
        self.dist_dir = self.rec_dir / "Distortion"
        self._ensure_directories()
        logger.debug(f"Audio session directory updated to: {session_path}")

    def _log_config(self):
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
            f.comment = title
            f.write(data)

    def _run_sweep(self) -> Dict[str, Any]:
        """
        Executes the playback and recording of the composite signal.
        Handles stream synchronization, multiple sweep averaging, and loopback alignment.
        """
        # 1. Generate Signals
        s_fund, phase, inv_sweep = self.sweep_gen.generate()

        s_composite = s_fund.copy()
        if self.harmonic_injector:
            s_composite = self.harmonic_injector.inject(s_fund, phase)

        # 2. Normalize Playback Signal
        target_amp = DSPUtils.db_to_lin(self.cap['sweep_level_dbfs'])
        max_val = np.max(np.abs(s_composite)) + 1e-12
        s_play = (s_composite * (target_amp / max_val)).astype(np.float32)

        # Apply a 1ms Hann fade to prevent the step discontinuity "BLIP" at the end
        s_play = DSPUtils.hann_fade(s_play, 1.0, self.hw['fs'], side="both")

        # 3. Apply Protection Filter (Playback Only)
        # This modifies what the speaker plays, but NOT the inverse filter. 
        # The resulting IR will inherently show the rolloff of this filter.
        if self.protection_filter:
            s_play = self.protection_filter.apply(s_play)

        # 4. Generate Alignment Marker
        # Goal: Generate band-limited marker. Pushing the fundamental frequency well 
        # below the HPF ensures only phase-flipped transient edges remain for sharp alignment.
        marker_single = self.marker_gen.generate()

        # 5. Construct Stream
        pre_samps_settle = int(round(self.cap['pre_sil_ms'] / 1000.0 * self.hw['fs']))
        post_samps = int(round(self.cap['post_sil_ms'] / 1000.0 * self.hw['fs']))
        sweep_len = len(s_play)
        marker_len = len(marker_single)

        # Calculate timeline
        slot_len = max(sweep_len, marker_len) + post_samps
        total_len = pre_samps_settle + (slot_len * self.cap['num_sweeps'])

        # Pre-allocate large buffers
        tx_sweep_long = np.zeros(total_len, dtype=np.float32)
        tx_ref_long = np.zeros(total_len, dtype=np.float32)

        # Populate buffers with repeated sweeps
        cursor = pre_samps_settle
        for _ in range(self.cap['num_sweeps']):
            tx_sweep_long[cursor: cursor + sweep_len] = s_play
            tx_ref_long[cursor: cursor + marker_len] = marker_single
            cursor += slot_len

        # 6. Setup Buffers & Devices
        out_ch_count = max(self.hw['ch_out_spkr'], self.hw['ch_out_ref']) + 1
        in_ch_count = max(self.hw['ch_in_mic'], self.hw['ch_in_loop']) + 1

        out_frames = np.zeros((total_len, out_ch_count), dtype=np.float32)
        out_frames[:, self.hw['ch_out_spkr']] = tx_sweep_long
        out_frames[:, self.hw['ch_out_ref']] = tx_ref_long

        rec_loop = np.zeros(total_len, dtype=np.float32)
        rec_mic = np.zeros(total_len, dtype=np.float32)

        out_api = self._get_api_name(self.hw['dev_out'])
        in_api = self._get_api_name(self.hw['dev_in'])
        use_asio_in, use_asio_out = ("ASIO" in in_api), ("ASIO" in out_api)

        # Configure SoundDevice Settings (ASIO vs WASAPI logic)
        if use_asio_in:
            in_args = (2, sd.AsioSettings(channel_selectors=[self.hw['ch_in_loop'], self.hw['ch_in_mic']]))
        else:
            in_args = (in_ch_count,
                       sd.WasapiSettings(exclusive=self.hw['wasapi_exclusive']) if "WASAPI" in in_api else None)

        if use_asio_out:
            out_args = (2, sd.AsioSettings(channel_selectors=[self.hw['ch_out_spkr'], self.hw['ch_out_ref']]))
        else:
            out_args = (out_ch_count,
                        sd.WasapiSettings(exclusive=self.hw['wasapi_exclusive']) if "WASAPI" in out_api else None)

        idx_play, idx_rec = 0, 0
        done_evt = threading.Event()

        # Real-time Callback
        def callback(indata, outdata, frames, time_info, status):
            nonlocal idx_play, idx_rec
            if status:
                logger.warning(f"Audio Status: {status}")

            # Output
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

            # Input
            n_in = min(frames, total_len - idx_rec)
            if n_in > 0:
                if use_asio_in:
                    rec_loop[idx_rec:idx_rec + n_in] = indata[:n_in, 0]
                    rec_mic[idx_rec:idx_rec + n_in] = indata[:n_in, 1]
                else:
                    rec_loop[idx_rec:idx_rec + n_in] = indata[:n_in, self.hw['ch_in_loop']]
                    rec_mic[idx_rec:idx_rec + n_in] = indata[:n_in, self.hw['ch_in_mic']]

            if use_asio_out:
                meter_out = _role_frames(outdata, 0, 1)
            else:
                meter_out = _role_frames(
                    outdata,
                    self.hw['ch_out_spkr'],
                    self.hw['ch_out_ref'],
                )
            if use_asio_in:
                meter_in = _role_frames(indata, 0, 1)
            else:
                meter_in = _role_frames(
                    indata,
                    self.hw['ch_in_loop'],
                    self.hw['ch_in_mic'],
                )
            update_audio_meter_state(
                meter_out,
                meter_in,
                active=True,
                sample_rate=self.hw['fs'],
            )

            idx_play += n_out
            idx_rec += n_in
            if idx_play >= total_len and idx_rec >= total_len:
                done_evt.set()

        # 7. Start Stream
        with sd.Stream(device=(self.hw['dev_in'], self.hw['dev_out']), samplerate=self.hw['fs'],
                       blocksize=self.hw['blocksize'],
                       dtype="float32", channels=(in_args[0], out_args[0]), dither_off=True,
                       extra_settings=(in_args[1], out_args[1]), callback=callback):
            done_evt.wait()
        reset_audio_meter_state(active=False)

        avg_mic, avg_loop, mic_slices, psr = self.alignment_engine.sync_and_average(
            rec_mic, rec_loop, marker_single, pre_samps_settle, slot_len, sweep_len
        )

        return {
            "inv_sweep": inv_sweep,
            "tx_ref_signal": tx_ref_long[:len(avg_mic)],
            "rx_mic_conditioned": avg_mic,
            "rx_loop_aligned": avg_loop,
            "debug_mic_slices": mic_slices,
            "psr": psr
        }

    def measure_ir(self, position: CylindricalPosition, order_id: str = "NA", save: bool = True):
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

        # 3. Debug Saves (Optional - write intermediate files)
        if save and self.cap['debug_saves']:
            logger.info("Saving debug artifacts...")
            self._save_wav_with_metadata(self.debug_dir / f"{base_name}_mic_conditioned.wav",
                                         result["rx_mic_conditioned"], f"{base_name}_mic_conditioned.wav")
            self._save_wav_with_metadata(self.debug_dir / f"{base_name}_loop_aligned.wav", result["rx_loop_aligned"],
                                         f"{base_name}_loop_aligned.wav")
            for i, slice_data in enumerate(result["debug_mic_slices"]):
                filename = f"{base_name}_sweep{i + 1:02d}.wav"
                self._save_wav_with_metadata(self.debug_dir / filename, slice_data, filename)

        # 4. Process IR (Deconvolution)
        ir_full, ir_linear = self.deconv_engine.process_ir(
            result["rx_mic_conditioned"], 
            result["inv_sweep"], 
            self.protection_filter
        )

        # 5. DSP Verification
        metrics = self.verifier.calculate_metrics(ir_full, ir_linear, result.get("psr", 0.0))
        logger.info(
            f"DSP Metrics: SNR={metrics['snr_db']:.1f}dB, THD={metrics['thd_pct']:.2f}%, PSR={metrics['psr']:.1f}")

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
                import json
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
            "metrics": metrics,
            "saved": save,
        }

    def play_sine(self, frequency: float, level_dbfs: float, duration_s: Optional[float] = 1.0) -> None:
        """
        Plays a sine wave at the specified frequency and level.

        :param frequency: Frequency in Hz.
        :param level_dbfs: Level in dBFS.
        :param duration_s: Duration in seconds. If None, plays until stop_sine() is called.
        """
        self.stop_sine()

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
        a_weight_b, a_weight_a = _a_weighting_filter(fs)
        a_weight_zi = np.zeros((2, max(len(a_weight_a), len(a_weight_b)) - 1), dtype=np.float64)
        c_weight_b, c_weight_a = _c_weighting_filter(fs)
        c_weight_zi = np.zeros((2, max(len(c_weight_a), len(c_weight_b)) - 1), dtype=np.float64)

        def weight_inputs(meter_in: np.ndarray, b: np.ndarray, a: np.ndarray, zi: np.ndarray) -> np.ndarray:
            weighted = np.zeros_like(meter_in, dtype=np.float32)
            for channel_index in range(min(2, meter_in.shape[1])):
                filtered, zi[channel_index] = scipy.signal.lfilter(
                    b,
                    a,
                    meter_in[:, channel_index],
                    zi=zi[channel_index],
                )
                weighted[:, channel_index] = filtered.astype(np.float32)
            return weighted

        in_ch_count = max(self.hw['ch_in_mic'], self.hw['ch_in_loop']) + 1
        if use_asio_in:
            in_args = (
                2,
                sd.AsioSettings(channel_selectors=[self.hw['ch_in_loop'], self.hw['ch_in_mic']]),
            )
        else:
            in_args = (
                in_ch_count,
                sd.WasapiSettings(exclusive=self.hw['wasapi_exclusive']) if "WASAPI" in in_api else None,
            )

        if duration_s is not None:
            n = int(round(duration_s * fs))
            t = np.arange(n) / fs
            sine = (np.sin(2 * np.pi * frequency * t) * target_amp).astype(np.float32)

            # Apply protection filter if configured
            if self.protection_filter:
                sine = self.protection_filter.apply(sine)

            if use_asio_out:
                # ASIO mapping: we want to play sine on ch_out_spkr.
                # channel_selectors maps outdata[:, 0] to spkr, outdata[:, 1] to ref
                out_args_extra = sd.AsioSettings(channel_selectors=[self.hw['ch_out_spkr'], self.hw['ch_out_ref']])
                out_data = np.zeros((n, 2), dtype=np.float32)
                out_data[:, 0] = sine
                out_data[:, 1] = sine
                out_ch_count = 2
            else:
                out_ch_count = max(self.hw['ch_out_spkr'], self.hw['ch_out_ref']) + 1
                out_args_extra = sd.WasapiSettings(exclusive=self.hw['wasapi_exclusive']) if "WASAPI" in out_api else None
                out_data = np.zeros((n, out_ch_count), dtype=np.float32)
                out_data[:, self.hw['ch_out_spkr']] = sine
                out_data[:, self.hw['ch_out_ref']] = sine

            idx_play = 0
            done_evt = threading.Event()

            def callback(indata, outdata, frames, time_info, status):
                nonlocal idx_play
                if status:
                    logger.warning(f"Sine Callback Status: {status}")
                n_out = min(frames, n - idx_play)
                if n_out > 0:
                    outdata[:n_out, :out_ch_count] = out_data[idx_play:idx_play + n_out, :out_ch_count]
                if frames > n_out:
                    outdata[n_out:] = 0
                if use_asio_out:
                    meter_out = _role_frames(outdata, 0, 1)
                else:
                    meter_out = _role_frames(
                        outdata,
                        self.hw['ch_out_spkr'],
                        self.hw['ch_out_ref'],
                    )
                if use_asio_in:
                    meter_in = _role_frames(indata, 0, 1)
                else:
                    meter_in = _role_frames(
                        indata,
                        self.hw['ch_in_loop'],
                        self.hw['ch_in_mic'],
                    )
                update_audio_meter_state(
                    meter_out,
                    meter_in,
                    active=True,
                    sample_rate=fs,
                    a_weighted_inputs=weight_inputs(meter_in, a_weight_b, a_weight_a, a_weight_zi),
                    c_weighted_inputs=weight_inputs(meter_in, c_weight_b, c_weight_a, c_weight_zi),
                )
                idx_play += n_out
                if idx_play >= n:
                    done_evt.set()

            with sd.Stream(
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
            reset_audio_meter_state(active=False)
        else:
            # Indefinite playback
            phase = 0.0
            phase_inc = 2 * np.pi * frequency / fs

            # For indefinite sine, we use the target_amp directly.
            # (Filtering and re-normalizing a steady-state sine wave with a linear filter
            # results in the same sine wave with a possible phase shift, which we ignore here).
            effective_amp = target_amp

            def callback(indata, outdata, frames, time_info, status):
                nonlocal phase
                if status:
                    logger.warning(f"Sine Callback Status: {status}")
                t = np.arange(frames)
                s = (np.sin(phase + phase_inc * t) * effective_amp).astype(np.float32)
                phase = (phase + phase_inc * frames) % (2 * np.pi)

                if use_asio_out:
                    outdata[:, 0] = s
                    outdata[:, 1] = s
                else:
                    outdata.fill(0)
                    outdata[:, self.hw['ch_out_spkr']] = s
                    outdata[:, self.hw['ch_out_ref']] = s
                if use_asio_out:
                    meter_out = _role_frames(outdata, 0, 1)
                else:
                    meter_out = _role_frames(
                        outdata,
                        self.hw['ch_out_spkr'],
                        self.hw['ch_out_ref'],
                    )
                if use_asio_in:
                    meter_in = _role_frames(indata, 0, 1)
                else:
                    meter_in = _role_frames(
                        indata,
                        self.hw['ch_in_loop'],
                        self.hw['ch_in_mic'],
                    )
                update_audio_meter_state(
                    meter_out,
                    meter_in,
                    active=True,
                    sample_rate=fs,
                    a_weighted_inputs=weight_inputs(meter_in, a_weight_b, a_weight_a, a_weight_zi),
                    c_weighted_inputs=weight_inputs(meter_in, c_weight_b, c_weight_a, c_weight_zi),
                )

            if use_asio_out:
                out_args_extra = sd.AsioSettings(channel_selectors=[self.hw['ch_out_spkr'], self.hw['ch_out_ref']])
                out_ch_count = 2
            else:
                out_ch_count = max(self.hw['ch_out_spkr'], self.hw['ch_out_ref']) + 1
                out_args_extra = sd.WasapiSettings(exclusive=self.hw['wasapi_exclusive']) if "WASAPI" in out_api else None

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

    def stop_sine(self) -> None:
        """Stops any active sine wave playback."""
        if self._sine_stream is not None:
            logger.info("Stopping indefinite sine playback")
            try:
                self._sine_stream.stop()
                self._sine_stream.close()
            except Exception as e:
                logger.debug(f"Error closing sine stream: {e}")
            self._sine_stream = None
        sd.stop()
        reset_audio_meter_state(active=False)


class MockInterfaceAudio(Audio):
    """Digital Twin loopback simulating hardware latency and filters."""

    def _run_sweep(self) -> Dict[str, Any]:
        # 1. Generate Signals (Identical to standard Audio class)
        s_fund, phase, inv_sweep = self.sweep_gen.generate()

        s_composite = s_fund.copy()
        if self.harmonic_injector:
            s_composite = self.harmonic_injector.inject(s_fund, phase)

        target_amp = DSPUtils.db_to_lin(self.cap['sweep_level_dbfs'])
        max_val = np.max(np.abs(s_composite)) + 1e-12
        s_play = (s_composite * (target_amp / max_val)).astype(np.float32)

        # Apply a 1ms Hann fade to prevent the step discontinuity "BLIP" at the end
        s_play = DSPUtils.hann_fade(s_play, 1.0, self.hw['fs'], side="both")

        if self.protection_filter:
            s_play = self.protection_filter.apply(s_play)

        marker_single = self.marker_gen.generate()

        # 2. Construct Stream Timelines
        pre_samps_settle = int(round(self.cap['pre_sil_ms'] / 1000.0 * self.hw['fs']))
        post_samps = int(round(self.cap['post_sil_ms'] / 1000.0 * self.hw['fs']))
        sweep_len = len(s_play)
        marker_len = len(marker_single)

        slot_len = max(sweep_len, marker_len) + post_samps
        # Add 50ms extra padding to total_len for simulated hardware latency (20ms) and FIR filter
        extra_padding = int(0.050 * self.hw['fs'])
        total_len = pre_samps_settle + (slot_len * self.cap['num_sweeps']) + extra_padding

        tx_sweep = np.zeros(total_len, dtype=np.float32)
        tx_ref = np.zeros(total_len, dtype=np.float32)

        cursor = pre_samps_settle
        for _ in range(self.cap['num_sweeps']):
            tx_sweep[cursor: cursor + sweep_len] = s_play
            tx_ref[cursor: cursor + marker_len] = marker_single
            cursor += slot_len

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

        # 4. --- ALIGNMENT & DECONVOLUTION ---
        avg_mic, avg_loop, mic_slices, psr = self.alignment_engine.sync_and_average(
            rec_mic, rec_loop, marker_single, pre_samps_settle, slot_len, sweep_len
        )

        return {
            "inv_sweep": inv_sweep,
            "tx_ref_signal": tx_ref[:len(avg_mic)],
            "rx_mic_conditioned": avg_mic,
            "rx_loop_aligned": avg_loop,
            "debug_mic_slices": mic_slices,
            "psr": psr
        }

    def play_sine(self, frequency: float, level_dbfs: float, duration_s: Optional[float] = 1.0) -> None:
        """
        Plays a sine wave at the specified frequency and level.

        :param frequency: Frequency in Hz.
        :param level_dbfs: Level in dBFS.
        :param duration_s: Duration in seconds. If None, plays until stop_sine() is called.
        """
        logger.info(
            f"[MOCK-INTERFACE] Playing sine: {frequency} Hz, {level_dbfs} dBFS, {'until stopped' if duration_s is None else f'for {duration_s} s'}")

    def stop_sine(self) -> None:
        """Stops sine playback."""
        logger.info("[MOCK-INTERFACE] Stopped sine playback")
        reset_audio_meter_state(active=False)


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY
# ─────────────────────────────────────────────────────────────────────────────

class AudioMock(IAudio):
    """Simulation class for when hardware is unavailable."""

    def measure_ir(self, position: CylindricalPosition, order_id: str = "NA", save: bool = True) -> None:
        """
        Measure the impulse response at a given position.

        :param position: The scanner position.
        :param order_id: An optional order ID for file naming.
        """
        logger.info(f"[MOCK] Measured {position}, ID={order_id}")
        # time.sleep(1.0)  # Simulate sweep duration

    def play_sine(self, frequency: float, level_dbfs: float, duration_s: Optional[float] = 1.0) -> None:
        """
        Plays a sine wave at the specified frequency and level.

        :param frequency: Frequency in Hz.
        :param level_dbfs: Level in dBFS.
        :param duration_s: Duration in seconds. If None, plays until stop_sine() is called.
        """
        logger.info(
            f"[MOCK] Playing sine: {frequency} Hz, {level_dbfs} dBFS, {'until stopped' if duration_s is None else f'for {duration_s} s'}")

    def stop_sine(self) -> None:
        """Stops sine playback."""
        logger.info("[MOCK] Stopped sine playback")
        reset_audio_meter_state(active=False)


class AudioFactory:
    """Parses config and performs Dependency Injection assembling."""

    @staticmethod
    def _get_required_config(config: configparser.ConfigParser, section: str, key: str, type_func):
        if not config.has_option(section, key):
            raise KeyError(f"Missing required config: [{section}] {key}")
        val = config.get(section, key).split('#')[0].split(';')[0].strip()
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
            return None if s.lower() == "none" else float(s)

        fs = AudioFactory._get_required_config(config, audio_section, 'fs', int)
        sweep_dur_s = AudioFactory._get_required_config(config, sweep_section, 'sweep_dur_s', float)
        sweep_level_dbfs = AudioFactory._get_required_config(config, sweep_section, 'sweep_level_dbfs', float)

        dev_in = AudioFactory._get_required_config(config, audio_section, 'in_dev', int)
        dev_out = AudioFactory._get_required_config(config, audio_section, 'out_dev', int)

        saved_in_name = config.get(audio_section, 'in_dev_name', fallback='').strip()
        saved_in_api = config.get(audio_section, 'in_dev_hostapi', fallback='').strip()
        saved_out_name = config.get(audio_section, 'out_dev_name', fallback='').strip()
        saved_out_api = config.get(audio_section, 'out_dev_hostapi', fallback='').strip()

        resolved_in = find_device_id_by_name(saved_in_name, saved_in_api, require_input=True)
        resolved_out = find_device_id_by_name(saved_out_name, saved_out_api, require_output=True)
        if resolved_in is not None and resolved_in != dev_in:
            logger.info(
                f"Input device '{saved_in_name}' moved from ID {dev_in} to ID {resolved_in}; using current ID."
            )
            dev_in = resolved_in
        if resolved_out is not None and resolved_out != dev_out:
            logger.info(
                f"Output device '{saved_out_name}' moved from ID {dev_out} to ID {resolved_out}; using current ID."
            )
            dev_out = resolved_out

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
            'naming_convention': config.get(sweep_section, 'naming_convention', fallback='dimitri').strip(),
            'debug_saves': AudioFactory._get_required_config(config, sweep_section, 'debug_saves', bool),
            'sweep_dur_s': sweep_dur_s,
            'sweep_level_dbfs': sweep_level_dbfs,
            'num_sweeps': AudioFactory._get_required_config(config, sweep_section, 'num_sweeps', int),
            'pre_sil_ms': AudioFactory._get_required_config(config, sweep_section, 'pre_sil_ms', float),
            'post_sil_ms': AudioFactory._get_required_config(config, sweep_section, 'post_sil_ms', float),
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
            hpf_corr_db_cap = config.getfloat(sweep_section, 'PROTECT_HPF_CORR_DB_CAP', fallback=12.0)
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
