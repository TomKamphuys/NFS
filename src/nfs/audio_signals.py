"""Shared sweep definitions, matched inverse, marker and playback filters.

This module has no audio-device or acquisition dependency.
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import scipy.signal
import scipy.fft
from loguru import logger

from .utils.dsp import DSPUtils

SINE_RMS_FROM_PEAK = 1.0 / np.sqrt(2.0)
_LINEAR_IR_ORIGIN_SAMPLE = 50
_EXPORT_FADE_FRACTION = 0.1

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

