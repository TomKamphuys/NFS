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
    * Driver Protection: Configurable High-Pass Filter (Minimum or Linear Phase) 
      applied to the playback signal to protect tweeters/drivers from LF damage.
    * Harmonic Injection: Debug feature to inject artificial H2/H3 into the 
      sweep to verify distortion analysis logic.
    * Robust Alignment: Uses a Barker-13 code for precise temporal alignment. 
      Supports multi-sweep averaging with sweep alignment either by the first 
      marker and expected sample index of subsequent sweeps, or per-sweep 
      alignment for robustness against driver glitches.
    * IR Separation: Separates the Linear IR from the full capture (containing 
      distortion) and saves both as separate files.
"""

import configparser
import os
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import numpy as np
import scipy.signal
import scipy.fft
import soundfile as sf

from loguru import logger

from .datatypes import CylindricalPosition

# Enable ASIO build of PortAudio in python-sounddevice (Windows).
# This environment variable triggers the loading of ASIO drivers if available.
os.environ["SD_ENABLE_ASIO"] = "1"
import sounddevice as sd  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

class DSPUtils:
    """Static utilities for pure mathematical operations."""

    @staticmethod
    def fmt_num_for_name(val) -> str:
        """Formats a number for safe filenames (e.g., 4.5 -> '4p5')."""
        if val is None: return "NA"
        return str(val).replace(".", "p")

    @staticmethod
    def db_to_lin(db: float) -> float:
        """Converts dBFS to linear amplitude scale."""
        return 10 ** (db / 20.0)

    @staticmethod
    def hann_fade(sig: np.ndarray, fade_ms: float, fs: int, side: str = "both") -> np.ndarray:
        """
        Applies a Hann window fade to the signal edges to prevent spectral leakage (clicks).
        
        Args:
            :param fs: sample rate
            :param fade_ms: fade duration in ms
            :param sig: signal to fade
            :param side: 'in' (start), 'out' (end), or 'both'.
        """
        n_fade = int(round(fade_ms / 1000.0 * fs))
        if n_fade <= 0 or n_fade >= len(sig):
            return sig

        # Generate ramp: 0 to 1 (half-cosine)
        ramp = 0.5 * (1 - np.cos(np.pi * np.arange(n_fade) / (n_fade - 1)))
        y = sig.copy()

        if side in ["both", "in"]:
            y[:n_fade] *= ramp

        if side in ["both", "out"]:
            # Fade out uses the reverse of the ramp
            y[-n_fade:] *= ramp[::-1]

        return y

    @staticmethod
    def rfft_xcorr(a: np.ndarray, b: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Fast Cross-Correlation via RFFT."""
        # Calculate next power of 2 for optimal FFT performance and linear convolution
        n = int(2 ** np.ceil(np.log2(len(a) + len(b) - 1)))

        # Transform signal 'a' and 'b' into the frequency domain
        A = np.fft.rfft(a, n=n)
        B = np.fft.rfft(b, n=n)

        # Multiply by complex conjugate to perform correlation, then return to time domain
        x = np.fft.irfft(A * np.conj(B), n=n)

        # Shift the zero-lag component to the center of the array
        x = np.roll(x, len(b) - 1)

        # Create an array of lag indices corresponding to the shifted signal
        lags = np.arange(-len(b) + 1, len(a))

        # Return the lag indices and the correlation result trimmed to the valid range
        return lags, x[:len(lags)]


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
        self.fs = fs
        self.dur_ms = dur_ms
        self.bw_hz = bw_hz
        self.level_dbfs = level_dbfs

    def generate(self) -> np.ndarray:
        chips = np.array([+1, +1, +1, +1, +1, -1, -1, +1, +1, -1, +1, -1, +1], dtype=np.float32)
        n = max(16, int(round(self.dur_ms / 1000.0 * self.fs)))

        # Interpolate chips to desired duration
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
        self.fs = fs
        self.T = duration_s
        self.f1 = f1
        self.level_dbfs = level_dbfs

    def generate(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        f2 = self.fs * 0.5
        n = int(round(self.T * self.fs))
        t = np.arange(n) / self.fs
        w1, w2 = 2 * np.pi * self.f1, 2 * np.pi * f2
        L = self.T / np.log(w2 / w1)

        # 1. Fundamental (Clean) - Used for generating the Inverse Filter
        phase = w1 * L * (np.exp(t / L) - 1.0)
        s_fund = np.sin(phase).astype(np.float64)

        # 2. Generate CLEAN Inverse (Using Time Reversal)
        # We use the clean fundamental for the inverse to avoid "baking in" the distortion 
        # or protection filter into the reference.
        envelope = np.exp(-t / L)  # Amplitude envelope to correct pink spectrum to white
        inv = s_fund[::-1] * envelope  # Time Reversal

        # Normalize Inverse in Frequency Domain to ensure unity gain convolution
        target_amp = DSPUtils.db_to_lin(self.level_dbfs)
        Nfft = int(2 ** np.ceil(np.log2(len(s_fund) + len(inv) - 1)))
        S_fft = np.fft.rfft(s_fund, n=Nfft)
        I_fft = np.fft.rfft(inv, n=Nfft)
        peak_val = np.max(np.abs(np.fft.irfft(S_fft * I_fft, n=Nfft)))

        inv /= (peak_val * target_amp + 1e-15)
        return s_fund, phase, inv


class HarmonicInjector:
    """Injects H2/H3 distortions using the sweep's phase array."""

    def __init__(self, h2_db: Optional[float] = None, h3_db: Optional[float] = None):
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
    """Applies MIN or LIN phase HPF to protect drivers."""

    def __init__(self, fs: int, freq_hz: float, order: int, phase_mode: str):
        self.fs = fs
        self.freq_hz = freq_hz
        self.order = order
        self.phase_mode = phase_mode

    def apply(self, sig: np.ndarray) -> np.ndarray:
        """
        Applies a High Pass Protection Filter to the signal to ensure driver safety.
        
        This filter is applied ONLY to the playback signal, not the inverse filter.
        """
        if self.freq_hz is None or self.freq_hz <= 0:
            return sig

        logger.info(f"► Applying Protection HPF: {self.freq_hz}Hz, Order={self.order}, Phase={self.phase_mode}")

        if self.phase_mode == "MIN":
            # Minimum Phase: Standard IIR Butterworth (SOS implementation for stability)
            sos = scipy.signal.butter(self.order, self.freq_hz, btype='hp', fs=self.fs, output='sos')
            return scipy.signal.sosfilt(sos, sig).astype(np.float32)

        elif self.phase_mode == "LIN":
            # Linear Phase: Frequency Domain Synthesis (Zero Phase)
            # We construct the exact Butterworth Magnitude response and apply it without phase shift.
            # This ensures the slope order is exactly as requested (e.g., 1st order = 6dB/oct).
            # Note: Standard sosfiltfilt would double the effective order; this method does not.
            n = len(sig)
            Nfft = int(2 ** np.ceil(np.log2(n + self.fs)))  # Pad generously to avoid time-domain wrap-around artifacts
            X = np.fft.rfft(sig, n=Nfft)
            freqs = np.fft.rfftfreq(Nfft, d=1.0 / self.fs)

            safe_f = np.maximum(freqs, 1e-9)

            # Butterworth Magnitude: |H(f)| = 1 / sqrt(1 + (fc/f)^(2*N))
            mag = 1.0 / np.sqrt(1.0 + (self.freq_hz / safe_f) ** (2 * self.order))
            mag[0] = 0.0  # Strict DC kill

            # Apply Magnitude Mask (Phase remains 0 for the filter -> Linear Phase overall)
            X_filtered = X * mag
            y = np.fft.irfft(X_filtered, n=Nfft)
            return y[:n].astype(np.float32)

        else:
            logger.warning(f"Unknown phase mode '{self.phase_mode}', skipping protection filter.")
            return sig


# ─────────────────────────────────────────────────────────────────────────────
#  PROCESSING ENGINES
# ─────────────────────────────────────────────────────────────────────────────

class AlignmentEngine:
    """Handles cross-correlation and synchronous averaging."""

    def __init__(self, fs: int, num_sweeps: int, align_to_first_marker: bool, mic_tail_taper_ms: float,
                 marker_dur_ms: float):
        self.fs = fs
        self.num_sweeps = num_sweeps
        self.align_to_first_marker = align_to_first_marker
        self.mic_tail_taper_ms = mic_tail_taper_ms
        self.marker_dur_ms = marker_dur_ms

    def _matched_filter_detect(self, x: np.ndarray, ref: np.ndarray, search_start: int = None, search_end: int = None):
        """
        Finds the best match of signal 'ref' within 'x' using a matched filter.
        Returns the lag (index) and the correlation coefficient.
        """
        lags, corr = DSPUtils.rfft_xcorr(x, ref)
        if search_start is None: 
            search_start = 0
        if search_end is None: 
            search_end = len(x) - 1

        m = (lags >= search_start) & (lags <= search_end)
        lags_sel, corr_sel = lags[m], corr[m]

        if len(lags_sel) == 0:
            return 0, 0.0
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

        return int(lags_sel[i]), peak_val

    def sync_and_average(self, rec_mic: np.ndarray, rec_loop: np.ndarray, marker_single: np.ndarray,
                         pre_samps_settle: int, slot_len: int, sweep_len: int) -> Tuple[np.ndarray, np.ndarray, List[np.ndarray]]:

        # --- Alignment & Averaging ---
        mic_slices = []
        loop_slices = []
        capture_len = sweep_len + int(round(self.mic_tail_taper_ms / 1000.0 * self.fs))

        # --- FIXED ALIGNMENT LOGIC ---
        # 1. Find global anchor (first marker) using Matched Filter
        search_limit = pre_samps_settle + slot_len
        k_first_marker, _ = self._matched_filter_detect(rec_loop, marker_single, search_end=search_limit)

        # The correlation peak IS the start.
        t0_first_sweep = k_first_marker
        logger.debug(f"Marker found at {k_first_marker}. Using this as T0.")

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
                k_local, _ = self._matched_filter_detect(rec_loop, marker_single, search_start=s_start,
                                                         search_end=s_end)
                start_idx = k_local

            end_idx = start_idx + capture_len
            if end_idx <= len(rec_mic) and start_idx >= 0:
                mic_slices.append(rec_mic[start_idx: end_idx].copy())
                loop_slices.append(rec_loop[start_idx: end_idx].copy())

        if not mic_slices:
            raise RuntimeError("No valid sweeps captured (Alignment failed).")

        # Synchronous Averaging to lower noise floor
        avg_mic = np.mean(mic_slices, axis=0)
        avg_loop = np.mean(loop_slices, axis=0)

        # Fade out tail using the unified _hann_fade (approx 10ms)
        avg_mic = DSPUtils.hann_fade(avg_mic, 10.0, self.fs, side="out")

        return avg_mic.astype(np.float32), avg_loop.astype(np.float32), mic_slices


class DeconvolutionEngine:
    """Handles FFT deconvolution, spectral masking, and Farina separation."""

    def __init__(self, fs: int):
        self.fs = fs

    def process_ir(self, mic_data: np.ndarray, inv_data: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
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
        Y = np.fft.rfft(mic_data, n=Nfft)
        I = np.fft.rfft(inv_data, n=Nfft)

        # --- Spectral Mask Generation ---
        freqs = np.fft.rfftfreq(Nfft, d=1.0 / self.fs)

        # LF Mask - Standard Butterworth @ 5Hz (Fixed per requirement)
        safe_freqs = np.maximum(freqs, 1e-9)
        lf_mask = 1.0 / np.sqrt(1.0 + (5.0 / safe_freqs) ** 2)
        lf_mask[0] = 0.0

        # HF Mask (Taper near Nyquist to avoid ringing)
        nyquist = self.fs / 2.0
        f_hf_start = 20000 if self.fs <= 48000 else 24000
        f_hf_end = nyquist * 0.88

        hf_mask = np.ones_like(freqs)
        idx_hf_start = np.searchsorted(freqs, f_hf_start)
        idx_hf_end = np.searchsorted(freqs, f_hf_end)

        if idx_hf_end > idx_hf_start:
            n = np.linspace(0, 1, idx_hf_end - idx_hf_start)
            hf_mask[idx_hf_start: idx_hf_end] = 0.5 * (1 + np.cos(np.pi * n))
        if idx_hf_end < len(hf_mask): hf_mask[idx_hf_end:] = 0.0

        mag_mask = lf_mask * hf_mask

        # Minimum Phase Complex Mask Generation (Cepstrum Method)
        # This creates a filter kernel that has the magnitude of 'mag_mask' but
        # minimum phase characteristics (energy concentrated at the start).
        mag_spec = np.maximum(mag_mask, 1e-12)
        log_mag = np.log(mag_spec)
        if Nfft % 2 == 0:
            log_mag_full = np.concatenate([log_mag, log_mag[-2:0:-1]])
        else:
            log_mag_full = np.concatenate([log_mag, log_mag[-1:0:-1]])

        cepstrum = np.fft.ifft(log_mag_full).real
        w = np.zeros(Nfft);
        w[0] = 1.0;
        mid = Nfft // 2
        if Nfft % 2 == 0:
            w[mid] = 1.0;
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
        split_idx = len(inv_data) - 5
        h_linear = h_full[split_idx: split_idx + len(inv_data)]

        return h_full, h_linear


# ─────────────────────────────────────────────────────────────────────────────
#  INTERFACES
# ─────────────────────────────────────────────────────────────────────────────

class IAudio(ABC):
    @abstractmethod
    def measure_ir(self, position: CylindricalPosition, order_id: str = "NA") -> None:
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

        self.sweep_gen = sweep_gen
        self.marker_gen = marker_gen
        self.alignment_engine = alignment_engine
        self.deconv_engine = deconv_engine
        self.harmonic_injector = harmonic_injector
        self.protection_filter = protection_filter

        # Directories
        self.rec_dir = Path("./Recordings")
        self.rec_dir.mkdir(exist_ok=True)

        if self.cap['debug_saves']:
            self.debug_dir = self.rec_dir / "debug"
            self.debug_dir.mkdir(exist_ok=True)

        self._log_config()

    def _log_config(self):
        logger.info(
            f"Audio Config: FS={self.hw['fs']}, Sweeps={self.cap['num_sweeps']}, Dur={self.cap['sweep_dur_s']}s")
        logger.info(f"Devices: In={self.hw['dev_in']}, Out={self.hw['dev_out']}")

    def _get_api_name(self, dev_index: int) -> str:
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
            # Re-peak to ensure we hit the target DBFS in the passband.
            # This prevents the HPF from essentially quieting the whole sweep if fundamental is low.
            new_max = np.max(np.abs(s_play)) + 1e-12
            s_play *= (target_amp / new_max)

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

        avg_mic, avg_loop, mic_slices = self.alignment_engine.sync_and_average(
            rec_mic, rec_loop, marker_single, pre_samps_settle, slot_len, sweep_len
        )

        return {
            "inv_sweep": inv_sweep,
            "tx_ref_signal": tx_ref_long[:len(avg_mic)],
            "rx_mic_conditioned": avg_mic,
            "rx_loop_aligned": avg_loop,
            "debug_mic_slices": mic_slices
        }

    def measure_ir(self, position: CylindricalPosition, order_id: str = "NA") -> None:
        """
        Public entry point. Coordinates capture, processing, and file saving.
        """
        logger.info(f"Measuring IR at {position} (ID: {order_id})")

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
        if self.cap['debug_saves']:
            logger.info("Saving debug artifacts...")
            self._save_wav_with_metadata(self.debug_dir / f"{base_name}_mic_conditioned.wav",
                                         result["rx_mic_conditioned"], f"{base_name}_mic_conditioned.wav")
            self._save_wav_with_metadata(self.debug_dir / f"{base_name}_loop_aligned.wav", result["rx_loop_aligned"],
                                         f"{base_name}_loop_aligned.wav")
            for i, slice_data in enumerate(result["debug_mic_slices"]):
                filename = f"{base_name}_sweep{i + 1:02d}.wav"
                self._save_wav_with_metadata(self.debug_dir / filename, slice_data, filename)

        # 4. Process IR (Deconvolution)
        ir_full, ir_linear = self.deconv_engine.process_ir(result["rx_mic_conditioned"], result["inv_sweep"])

        # 5. Save Final Files
        # Main (Linear)
        linear_path = self.rec_dir / main_file_name
        self._save_wav_with_metadata(linear_path, ir_linear, main_file_name, subtype='FLOAT')
        logger.info(f"Saved Linear IR: {linear_path.name}")

        # Secondary (Distortion)
        dist_path = self.rec_dir / dist_file_name
        self._save_wav_with_metadata(dist_path, ir_full, dist_file_name, subtype='FLOAT')
        logger.info(f"Saved Distortion IR: {dist_path.name}")


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
            new_max = np.max(np.abs(s_play)) + 1e-12
            s_play *= (target_amp / new_max)

        marker_single = self.marker_gen.generate()

        # 2. Construct Stream Timelines
        pre_samps_settle = int(round(self.cap['pre_sil_ms'] / 1000.0 * self.hw['fs']))
        post_samps = int(round(self.cap['post_sil_ms'] / 1000.0 * self.hw['fs']))
        sweep_len = len(s_play)
        marker_len = len(marker_single)

        slot_len = max(sweep_len, marker_len) + post_samps
        total_len = pre_samps_settle + (slot_len * self.cap['num_sweeps'])

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
        #  fir_taps = np.array([1.0], dtype=np.float32)

        # B) 15Hz 1st-Order HPF (10k Ohm + 10uF RC circuit)
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
        avg_mic, avg_loop, mic_slices = self.alignment_engine.sync_and_average(
            rec_mic, rec_loop, marker_single, pre_samps_settle, slot_len, sweep_len
        )

        return {
            "inv_sweep": inv_sweep,
            "tx_ref_signal": tx_ref[:len(avg_mic)],
            "rx_mic_conditioned": avg_mic,
            "rx_loop_aligned": avg_loop,
            "debug_mic_slices": mic_slices
        }


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY
# ─────────────────────────────────────────────────────────────────────────────

class AudioMock(IAudio):
    """Simulation class for when hardware is unavailable."""

    def measure_ir(self, position: CylindricalPosition, order_id: str = "NA") -> None:
        logger.info(f"[MOCK] Measured {position}, ID={order_id}")
        time.sleep(1.0)  # Simulate sweep duration


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

        sweep_section = 'sweep'
        if not config.has_section(sweep_section):
            raise KeyError(f"Config file missing [{sweep_section}] section")

        def parse_optional_float(s):
            return None if s.lower() == "none" else float(s)

        fs = AudioFactory._get_required_config(config, audio_section, 'fs', int)
        sweep_dur_s = AudioFactory._get_required_config(config, sweep_section, 'sweep_dur_s', float)
        sweep_level_dbfs = AudioFactory._get_required_config(config, sweep_section, 'sweep_level_dbfs', float)

        hw_config = {
            'dev_in': AudioFactory._get_required_config(config, audio_section, 'in_dev', int),
            'dev_out': AudioFactory._get_required_config(config, audio_section, 'out_dev', int),
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
            phase_mode = AudioFactory._get_required_config(config, sweep_section, 'PROTECT_HPF_PHASE', str)
            filter_engine = ProtectionFilter(fs, protect_hz, order, phase_mode)
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
