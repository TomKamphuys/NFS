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
import sounddevice as sd

# ─────────────────────────────────────────────────────────────────────────────
#  HELPER FUNCTIONS (Signal Generation & Formatting)
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_num_for_name(val):
    """Formats a number for safe filenames (e.g., 4.5 -> '4p5')."""
    if val is None: return "NA"
    return str(val).replace(".", "p")

def _db_to_lin(db): 
    """Converts dBFS to linear amplitude scale."""
    return 10 ** (db / 20.0)

def _hann_fade(sig: np.ndarray, fade_ms: float, fs: int, side: str = "both") -> np.ndarray:
    """
    Applies a Hann window fade to the signal edges to prevent spectral leakage (clicks).
    
    Args:
        side: 'in' (start), 'out' (end), or 'both'.
    """
    n_fade = int(round(fade_ms / 1000.0 * fs))
    if n_fade <= 0 or n_fade >= len(sig): return sig
    
    # Generate ramp: 0 to 1 (half-cosine)
    ramp = 0.5 * (1 - np.cos(np.pi * np.arange(n_fade) / (n_fade - 1)))
    y = sig.copy()
    
    if side in ["both", "in"]:
        y[:n_fade] *= ramp
    
    if side in ["both", "out"]:
        # Fade out uses the reverse of the ramp
        y[-n_fade:] *= ramp[::-1]
        
    return y

def _apply_protection_hpf(sig: np.ndarray, fs: int, freq_hz: float, order: int, phase_mode: str) -> np.ndarray:
    """
    Applies a High Pass Protection Filter to the signal to ensure driver safety.
    
    This filter is applied ONLY to the playback signal, not the inverse filter.
    
    Args:
        freq_hz: Corner frequency (-3dB point).
        order: Filter order (1 to 4).
        phase_mode: 
            'MIN' (Minimum Phase): Standard IIR Butterworth. Causal, introduces phase shift.
            'LIN' (Linear/Zero Phase): Frequency Domain Synthesis. Non-causal, preserves 
                                       phase alignment (constant group delay).
    """
    if freq_hz is None or freq_hz <= 0:
        return sig

    logger.info(f"► Applying Protection HPF: {freq_hz}Hz, Order={order}, Phase={phase_mode}")

    if phase_mode == "MIN":
        # Minimum Phase: Standard IIR Butterworth (SOS implementation for stability)
        sos = scipy.signal.butter(order, freq_hz, btype='hp', fs=fs, output='sos')
        return scipy.signal.sosfilt(sos, sig).astype(np.float32)

    elif phase_mode == "LIN":
        # Linear Phase: Frequency Domain Synthesis (Zero Phase)
        # We construct the exact Butterworth Magnitude response and apply it without phase shift.
        # This ensures the slope order is exactly as requested (e.g., 1st order = 6dB/oct).
        # Note: Standard sosfiltfilt would double the effective order; this method does not.
        
        n = len(sig)
        Nfft = int(2**np.ceil(np.log2(n + fs))) # Pad generously to avoid time-domain wrap-around artifacts
        X = np.fft.rfft(sig, n=Nfft)
        freqs = np.fft.rfftfreq(Nfft, d=1.0/fs)
        
        safe_f = np.maximum(freqs, 1e-9)
        
        # Butterworth Magnitude: |H(f)| = 1 / sqrt(1 + (fc/f)^(2*N))
        mag = 1.0 / np.sqrt(1.0 + (freq_hz / safe_f)**(2 * order))
        mag[0] = 0.0 # Strict DC kill
        
        # Apply Magnitude Mask (Phase remains 0 for the filter -> Linear Phase overall)
        X_filtered = X * mag
        
        y = np.fft.irfft(X_filtered, n=Nfft)
        return y[:n].astype(np.float32)
    
    else:
        logger.warning(f"Unknown phase mode '{phase_mode}', skipping protection filter.")
        return sig

def _make_exp_sweep(fs, T, f1=1.0, level_dbfs=-6.0, h2_db=None, h3_db=None,
                    protect_hz=None, protect_order=2, protect_phase="MIN"):
    """
    Generates an Exponential Sine Sweep (ESS) and its CLEAN inverse filter.
    
    Implements the Farina Method:
      1. Generates the fundamental sweep.
      2. Optionally injects Harmonics (H2, H3) for testing distortion analysis.
      3. Applies Protection HPF to the PLAYBACK signal only.
      4. Generates a 'clean' Inverse Filter using Time Reversal of the fundamental.
      
    Returns:
        s_play: The signal to send to the DAC (potentially filtered/distorted).
        inv:    The ideal inverse filter for deconvolution.
    """
    f2 = fs * 0.5
    n = int(round(T * fs))
    t = np.arange(n) / fs
    w1, w2 = 2 * np.pi * f1, 2 * np.pi * f2
    L = T / np.log(w2 / w1)
    
    # 1. Fundamental (Clean) - Used for generating the Inverse Filter
    phase = w1 * L * (np.exp(t / L) - 1.0)
    s_fund = np.sin(phase).astype(np.float64)
    
    # 2. Composite (Playback) - Used for Excitation
    s_composite = s_fund.copy()
    
    # --- HARMONIC INJECTION (TESTING) ---
    # Adds artificial distortion to verify that the Farina separation logic works.
    if h2_db is not None:
        amp_h2 = _db_to_lin(h2_db)
        logger.info(f"► INJECTING H2 @ {h2_db} dB")
        s_composite += amp_h2 * np.sin(2 * phase)
        
    if h3_db is not None:
        amp_h3 = _db_to_lin(h3_db)
        logger.info(f"► INJECTING H3 @ {h3_db} dB")
        s_composite += amp_h3 * np.sin(3 * phase)
    # ------------------------------------
    
    # 3. Normalize Playback Signal
    target_amp = _db_to_lin(level_dbfs)
    max_val = np.max(np.abs(s_composite)) + 1e-12
    s_play = (s_composite * (target_amp / max_val)).astype(np.float32)

    # 4. Apply Protection Filter (Playback Only)
    # This modifies what the speaker plays, but NOT the inverse filter. 
    # The resulting IR will inherently show the rolloff of this filter.
    if protect_hz is not None and protect_hz > 0:
        s_play = _apply_protection_hpf(s_play, fs, protect_hz, protect_order, protect_phase)
        
        # Re-peak to ensure we hit the target DBFS in the passband.
        # This prevents the HPF from essentially quieting the whole sweep if fundamental is low.
        new_max = np.max(np.abs(s_play)) + 1e-12
        s_play *= (target_amp / new_max)

    # 5. Generate CLEAN Inverse (Using Time Reversal)
    # We use the clean fundamental for the inverse to avoid "baking in" the distortion 
    # or protection filter into the reference.
    envelope = np.exp(-t / L)   # Amplitude envelope to correct pink spectrum to white
    inv = s_fund[::-1] * envelope # Time Reversal
    
    # Normalize Inverse in Frequency Domain to ensure unity gain convolution
    Nfft = int(2**np.ceil(np.log2(len(s_fund) + len(inv) - 1)))
    S_fft = np.fft.rfft(s_fund, n=Nfft)
    I_fft = np.fft.rfft(inv, n=Nfft)
    peak_val = np.max(np.abs(np.fft.irfft(S_fft * I_fft, n=Nfft)))
    
    inv /= (peak_val * target_amp + 1e-15) 
    return s_play, inv

def _make_barker13_marker(fs, dur_ms, bw_hz, level_dbfs):
    """
    Generates a band-limited Barker-13 sequence for precise temporal alignment.
    Barker codes have ideal autocorrelation properties (low sidelobes), making them
    superior to simple pulses for synchronization in noisy environments.
    """
    chips = np.array([+1,+1,+1,+1,+1,-1,-1,+1,+1,-1,+1,-1,+1], dtype=np.float32)
    n = max(16, int(round(dur_ms / 1000.0 * fs)))
    
    # Interpolate chips to desired duration
    marker_raw = np.interp(np.linspace(0, len(chips) - 1, n), np.arange(chips.size), chips)
    
    # Taper edges using the unified _hann_fade
    marker_raw = _hann_fade(marker_raw.astype(np.float32), 1.0, fs)

    # Frequency Domain Band-Limiting
    # Ensures the marker doesn't excite resonances outside the measurement band
    Nfft = int(2 ** np.ceil(np.log2(n * 2)))
    M = np.fft.rfft(marker_raw, n=Nfft)
    freqs = np.fft.rfftfreq(Nfft, 1 / fs)
    f_lo, f_hi = bw_hz
    mask = np.ones_like(freqs, dtype=np.float32)
    
    # Apply cosine ramps at band edges
    if f_lo > 0:
        idx = freqs < f_lo
        ramp = 0.5 * (1 - np.cos(np.pi * np.clip(freqs[idx] / max(1e-9, f_lo), 0, 1)))
        mask[idx] = ramp ** 2
    if f_hi < fs / 2:
        idx = freqs > f_hi
        ramp = 0.5 * (1 - np.cos(np.pi * np.clip((fs / 2 - freqs[idx]) / max(1e-9, (fs / 2 - f_hi)), 0, 1)))
        mask[idx] = ramp ** 2
        
    marker_bl = np.fft.irfft(M * mask, n=Nfft)[:n].astype(np.float32)
    marker_bl *= _db_to_lin(level_dbfs) / (np.max(np.abs(marker_bl)) + 1e-12)
    return marker_bl

def _rfft_xcorr(a, b):
    """Fast Cross-Correlation via RFFT."""
    n = int(2 ** np.ceil(np.log2(len(a) + len(b) - 1)))
    A = np.fft.rfft(a, n=n)
    B = np.fft.rfft(b, n=n)
    x = np.fft.irfft(A * np.conj(B), n=n)
    x = np.roll(x, len(b) - 1)
    lags = np.arange(-len(b) + 1, len(a))
    return lags, x[:len(lags)]

def _matched_filter_detect(x, ref, search_start=None, search_end=None):
    """
    Finds the best match of signal 'ref' within 'x' using a matched filter.
    Returns the lag (index) and the correlation coefficient.
    """
    lags, corr = _rfft_xcorr(x, ref)
    if search_start is None: search_start = 0
    if search_end is None: search_end = len(x) - 1
    
    m = (lags >= search_start) & (lags <= search_end)
    lags_sel, corr_sel = lags[m], corr[m]
    
    if len(lags_sel) == 0: return 0, 0.0
    i = int(np.argmax(corr_sel))
    return int(lags_sel[i]), float(corr_sel[i])


# ─────────────────────────────────────────────────────────────────────────────
#  INTERFACES
# ─────────────────────────────────────────────────────────────────────────────

class IAudio(ABC):
    @abstractmethod
    def measure_ir(self, position: CylindricalPosition, order_id: str = "NA") -> None:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  CORE AUDIO CLASS
# ─────────────────────────────────────────────────────────────────────────────

class Audio(IAudio):
    def __init__(self,
                 device_in_id: int,
                 device_out_id: int,
                 ch_in_mic: int,
                 ch_in_loop: int,
                 ch_out_spkr: int,
                 ch_out_ref: int,
                 fs: int,
                 sweep_dur_s: float,
                 sweep_level_dbfs: float,
                 num_sweeps: int,
                 pre_sil_ms: float,
                 post_sil_ms: float,
                 mic_tail_taper_ms: float,
                 align_to_first_marker: bool,
                 blocksize: int,
                 wasapi_exclusive: bool,
                 debug_saves: bool,
                 protect_hpf_hz: Optional[float],
                 protect_hpf_order: int,
                 protect_hpf_phase: str,
                 h2_test_db: Optional[float] = None,
                 h3_test_db: Optional[float] = None):
        
        # Device & Channel Mapping
        self.dev_in = device_in_id
        self.dev_out = device_out_id
        self.ch_in_mic = ch_in_mic
        self.ch_in_loop = ch_in_loop
        self.ch_out_spkr = ch_out_spkr
        self.ch_out_ref = ch_out_ref
        
        # Capture Configuration
        self.fs = fs
        self.sweep_dur_s = sweep_dur_s
        self.sweep_level_dbfs = sweep_level_dbfs
        self.num_sweeps = num_sweeps            # Number of sweeps to average
        self.pre_sil_ms = pre_sil_ms
        self.post_sil_ms = post_sil_ms
        self.mic_tail_taper_ms = mic_tail_taper_ms
        
        # Alignment Strategy
        # True = Find 1st marker, assume perfect clock for subsequent sweeps.
        # False = Re-run matched filter for every sweep (corrects clock drift).
        self.align_to_first_marker = align_to_first_marker
        
        self.blocksize = blocksize
        self.wasapi_exclusive = wasapi_exclusive
        self.debug_saves = debug_saves
        
        # Protection Filter Config
        self.protect_hpf_hz = protect_hpf_hz
        self.protect_hpf_order = protect_hpf_order
        self.protect_hpf_phase = protect_hpf_phase

        # Test Parameters (Harmonic Injection)
        self.h2_test_db = h2_test_db
        self.h3_test_db = h3_test_db
        
        # Directories
        self.rec_dir = Path("./Recordings")
        self.rec_dir.mkdir(exist_ok=True)
        
        if self.debug_saves:
            self.debug_dir = self.rec_dir / "debug"
            self.debug_dir.mkdir(exist_ok=True)

        self._log_config()

    def _log_config(self):
        logger.info(f"Audio Config: FS={self.fs}, Sweeps={self.num_sweeps}, Dur={self.sweep_dur_s}s")
        logger.info(f"Devices: In={self.dev_in}, Out={self.dev_out}")
        logger.info(f"Protection: HPF={self.protect_hpf_hz}Hz, Order={self.protect_hpf_order}, Phase={self.protect_hpf_phase}")
        if self.h2_test_db is not None or self.h3_test_db is not None:
             logger.warning(f"TEST MODE: Injecting Harmonics (H2={self.h2_test_db}dB, H3={self.h3_test_db}dB)")

    def _get_api_name(self, dev_index: int) -> str:
        try:
            d = sd.query_devices(dev_index)
            return sd.query_hostapis()[d['hostapi']]['name'].upper()
        except:
            return "UNKNOWN"

    # ─────────────────────────────────────────────────────────────────────────
    #  STEP 1: SWEEP EXECUTION (The "Black Box")
    # ─────────────────────────────────────────────────────────────────────────

    def _run_sweep(self) -> Dict[str, Any]:
        """
        Executes the playback and recording of the composite signal.
        Handles stream synchronization, multiple sweep averaging, and loopback alignment.
        """
        # 1. Generate Signals
        # Create the Farina Sweep (with optional protection & harmonic injection)
        # Hardcoded: f1=1.0 per requirement
        sweep_single, inv_sweep = _make_exp_sweep(
            self.fs, self.sweep_dur_s, f1=1.0, level_dbfs=self.sweep_level_dbfs,
            h2_db=self.h2_test_db, h3_db=self.h3_test_db,
            protect_hz=self.protect_hpf_hz, 
            protect_order=self.protect_hpf_order, 
            protect_phase=self.protect_hpf_phase
        )
        # Hardcoded: bw_hz=(500 Hz -> 15000 Hz). Duration 0.5 S (500 ms).
        # Goal - push the fundamental frequency (13 chirps / duration 0.5 S = 26 Hz) well
        # below the 500hz HPF, so only phase flipped transint edges remain for sharp alignemnet. 
        marker_single = _make_barker13_marker(self.fs, 500.0, (500.0, 15000.0), self.sweep_level_dbfs)

        # 2. Construct Stream
        pre_samps_settle = int(round(self.pre_sil_ms  / 1000.0 * self.fs))
        post_samps       = int(round(self.post_sil_ms / 1000.0 * self.fs))
        sweep_len        = len(sweep_single)
        marker_len       = len(marker_single)
        
        # Calculate timeline
        slot_len = max(sweep_len, marker_len) + post_samps
        total_len = pre_samps_settle + (slot_len * self.num_sweeps)
        
        # Pre-allocate large buffers
        tx_sweep_long = np.zeros(total_len, dtype=np.float32)
        tx_ref_long   = np.zeros(total_len, dtype=np.float32)
        
        # Populate buffers with repeated sweeps
        cursor = pre_samps_settle
        for _ in range(self.num_sweeps):
            tx_sweep_long[cursor : cursor+sweep_len] = sweep_single
            tx_ref_long[cursor : cursor+marker_len]  = marker_single
            cursor += slot_len

        # 3. Setup Buffers & Devices
        out_ch_count = max(self.ch_out_spkr, self.ch_out_ref) + 1
        in_ch_count  = max(self.ch_in_mic,  self.ch_in_loop)  + 1

        out_frames = np.zeros((total_len, out_ch_count), dtype=np.float32)
        out_frames[:, self.ch_out_spkr] = tx_sweep_long
        out_frames[:, self.ch_out_ref]  = tx_ref_long

        rec_loop = np.zeros(total_len, dtype=np.float32)
        rec_mic  = np.zeros(total_len, dtype=np.float32)

        out_api = self._get_api_name(self.dev_out)
        in_api  = self._get_api_name(self.dev_in)
        use_asio_in, use_asio_out = ("ASIO" in in_api), ("ASIO" in out_api)

        # Configure SoundDevice Settings (ASIO vs WASAPI logic)
        if use_asio_in: in_args = (2, sd.AsioSettings(channel_selectors=[self.ch_in_loop, self.ch_in_mic]))
        else: in_args = (in_ch_count, sd.WasapiSettings(exclusive=self.wasapi_exclusive) if "WASAPI" in in_api else None)

        if use_asio_out: out_args = (2, sd.AsioSettings(channel_selectors=[self.ch_out_spkr, self.ch_out_ref]))
        else: out_args = (out_ch_count, sd.WasapiSettings(exclusive=self.wasapi_exclusive) if "WASAPI" in out_api else None)

        idx_play, idx_rec = 0, 0
        done_evt = threading.Event()
        
        # Real-time Callback
        def callback(indata, outdata, frames, time_info, status):
            nonlocal idx_play, idx_rec
            if status: logger.warning(f"Audio Status: {status}")

            # Output
            n_out = min(frames, total_len - idx_play)
            if use_asio_out:
                outdata[:n_out, 0] = out_frames[idx_play:idx_play+n_out, self.ch_out_spkr]
                outdata[:n_out, 1] = out_frames[idx_play:idx_play+n_out, self.ch_out_ref]
                if frames > n_out: outdata[n_out:] = 0
            else:
                outdata[:n_out, :out_args[0]] = out_frames[idx_play:idx_play+n_out, :out_args[0]]
                if frames > n_out: outdata[n_out:] = 0

            # Input
            n_in = min(frames, total_len - idx_rec)
            if n_in > 0:
                if use_asio_in:
                    rec_loop[idx_rec:idx_rec+n_in] = indata[:n_in, 0]
                    rec_mic[idx_rec:idx_rec+n_in]  = indata[:n_in, 1]
                else:
                    rec_loop[idx_rec:idx_rec+n_in] = indata[:n_in, self.ch_in_loop]
                    rec_mic[idx_rec:idx_rec+n_in]  = indata[:n_in, self.ch_in_mic]
            
            idx_play += n_out
            idx_rec  += n_in
            if idx_play >= total_len and idx_rec >= total_len: 
                done_evt.set()

        # 4. Start Stream
        with sd.Stream(device=(self.dev_in, self.dev_out), samplerate=self.fs, blocksize=self.blocksize,
                       dtype="float32", channels=(in_args[0], out_args[0]), dither_off=True,
                       extra_settings=(in_args[1], out_args[1]), callback=callback):
            done_evt.wait()
            
        # 5. Alignment & Averaging
        mic_slices = []
        loop_slices = []
        capture_len = sweep_len + int(round(self.mic_tail_taper_ms/1000.0 * self.fs))
        
        # --- FIXED ALIGNMENT LOGIC ---
        # 1. Find global anchor (first marker) using Matched Filter
        search_limit = pre_samps_settle + slot_len 
        k_first_marker, _ = _matched_filter_detect(rec_loop, marker_single, search_end=search_limit)
        
        # In sweep_function.py, the correlation peak IS the start.
        t0_first_sweep = k_first_marker
        
        logger.debug(f"Marker found at {k_first_marker}. Using this as T0.")
        
        window_samps = int(0.005 * self.fs) # 5ms search window for re-sync
        
        for i in range(self.num_sweeps):
            expected_t0 = t0_first_sweep + (i * slot_len)
            
            if self.align_to_first_marker:
                # Sample-based cut: Rely on the first marker and constant sample rate
                start_idx = expected_t0
            else:
                # Per-sweep alignment: Re-sync to the marker for *every* sweep
                # Corrects for minor clock drift in very long sequences
                s_start = max(0, expected_t0 - window_samps)
                s_end   = min(len(rec_loop), expected_t0 + window_samps)
                
                k_local, _ = _matched_filter_detect(rec_loop, marker_single, search_start=s_start, search_end=s_end)
                start_idx = k_local

            end_idx = start_idx + capture_len
            if end_idx <= len(rec_mic) and start_idx >= 0:
                mic_slices.append(rec_mic[start_idx : end_idx].copy())
                loop_slices.append(rec_loop[start_idx : end_idx].copy())

        if not mic_slices:
            raise RuntimeError("No valid sweeps captured (Alignment failed).")

        # Synchronous Averaging to lower noise floor
        avg_mic  = np.mean(mic_slices, axis=0)
        avg_loop = np.mean(loop_slices, axis=0)
        
        # Fade out tail using the unified _hann_fade (approx 10ms)
        avg_mic = _hann_fade(avg_mic, 10.0, self.fs, side="out")

        return {
            "inv_sweep": inv_sweep,
            "tx_ref_signal": tx_ref_long[:len(avg_mic)], 
            "rx_mic_conditioned": avg_mic.astype(np.float32), 
            "rx_loop_aligned": avg_loop.astype(np.float32),
            "debug_mic_slices": mic_slices 
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  STEP 2: PROCESSING (The "Math")
    # ─────────────────────────────────────────────────────────────────────────

    def _process_ir(self, mic_data: np.ndarray, inv_data: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
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
        Nfft = int(2**np.ceil(np.log2(n_conv)))
        
        # Go to Frequency Domain
        Y = np.fft.rfft(mic_data, n=Nfft)
        I = np.fft.rfft(inv_data, n=Nfft)
        
        # --- Spectral Mask Generation ---
        freqs = np.fft.rfftfreq(Nfft, d=1.0/self.fs)
        
        # LF Mask - Standard Butterworth @ 5Hz (Fixed per requirement)
        safe_freqs = np.maximum(freqs, 1e-9) 
        lf_mask = 1.0 / np.sqrt(1.0 + (5.0 / safe_freqs)**2) # Change HPF frequency here <-
        lf_mask[0] = 0.0
            
        # HF Mask (Taper near Nyquist to avoid ringing)
        nyquist = self.fs / 2.0
        f_hf_start = 20000 if self.fs <= 48000 else 24000
        f_hf_end = nyquist * 0.88
        
        hf_mask = np.ones_like(freqs)
        idx_hf_start = np.searchsorted(freqs, f_hf_start)
        idx_hf_end   = np.searchsorted(freqs, f_hf_end)
        
        if idx_hf_end > idx_hf_start:
            n = np.linspace(0, 1, idx_hf_end - idx_hf_start)
            hf_mask[idx_hf_start : idx_hf_end] = 0.5 * (1 + np.cos(np.pi * n))
        if idx_hf_end < len(hf_mask): hf_mask[idx_hf_end:] = 0.0
        
        mag_mask = lf_mask * hf_mask
        
        # Minimum Phase Complex Mask Generation (Cepstrum Method)
        # This creates a filter kernel that has the magnitude of 'mag_mask' but
        # minimum phase characteristics (energy concentrated at start).
        mag_spec = np.maximum(mag_mask, 1e-12)
        log_mag = np.log(mag_spec)
        if Nfft % 2 == 0: log_mag_full = np.concatenate([log_mag, log_mag[-2:0:-1]])
        else:             log_mag_full = np.concatenate([log_mag, log_mag[-1:0:-1]])
        
        cepstrum = np.fft.ifft(log_mag_full).real
        w = np.zeros(Nfft); w[0] = 1.0; mid = Nfft // 2
        if Nfft % 2 == 0: w[mid] = 1.0; w[1:mid] = 2.0
        else:             w[1:mid+1] = 2.0
        
        H_min_phase = np.exp(np.fft.fft(cepstrum * w))[:len(mag_spec)]
        
        # Deconvolve & Apply Mask
        I_filtered = I * H_min_phase
        H_complex = Y * I_filtered
        h_full = np.fft.irfft(H_complex, n=Nfft).astype(np.float32)
        
        # --- Farina Separation (Linear vs Distortion) ---
        # The linear response is at the end of the buffer (circular convolution wrap) 
        # or casually placed depending on padding.
        # With the Inverse Filter generated via Time Reversal, the linear peak is 
        # usually aligned such that we slice the tail.
        split_idx = len(inv_data) - 5
        if len(h_full) > split_idx:
            h_linear = h_full[split_idx:]
        else:
            h_linear = h_full
            
        return h_full, h_linear

    # ─────────────────────────────────────────────────────────────────────────
    #  STEP 3: ORCHESTRATION (Public API)
    # ─────────────────────────────────────────────────────────────────────────

    def measure_ir(self, position: CylindricalPosition, order_id: str = "NA") -> None:
        """
        Public entry point. Coordinates capture, processing, and file saving.
        """
        logger.info(f"Measuring IR at {position} (ID: {order_id})")
        
        # 1. Capture Raw Data (Run Sweeps)
        result = self._run_sweep()
        
        # 2. Filename formatting
        base_name = (
            f"{order_id}_"
            f"r{_fmt_num_for_name(position.r())}_"
            f"ph{_fmt_num_for_name(position.t())}_"
            f"z{_fmt_num_for_name(position.z())}"
        )

        # 3. Debug Saves (Optional - write intermediate files)
        if self.debug_saves:
            logger.info("Saving debug artifacts...")
            sf.write(str(self.debug_dir / f"{base_name}_mic_conditioned.wav"), result["rx_mic_conditioned"], self.fs)
            sf.write(str(self.debug_dir / f"{base_name}_loop_aligned.wav"), result["rx_loop_aligned"], self.fs)
            
            for i, slice_data in enumerate(result["debug_mic_slices"]):
                sf.write(str(self.debug_dir / f"{base_name}_sweep{i+1:02d}.wav"), slice_data, self.fs)

        # 4. Process IR (Deconvolution)
        ir_full, ir_linear = self._process_ir(result["rx_mic_conditioned"], result["inv_sweep"])
        
        # 5. Save Final Files
        # Main (Linear)
        linear_path = self.rec_dir / f"{base_name}_ir.wav"
        sf.write(str(linear_path), ir_linear, self.fs, subtype='FLOAT')
        logger.info(f"Saved Linear IR: {linear_path.name}")
        
        # Secondary (Distortion) - useful for analysing non-linearities
        dist_path = self.rec_dir / f"{base_name}_ir_dist.wav"
        sf.write(str(dist_path), ir_full, self.fs, subtype='FLOAT')
        logger.info(f"Saved Distortion IR: {dist_path.name}")


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY
# ─────────────────────────────────────────────────────────────────────────────

class AudioMock(IAudio):
    """Simulation class for when hardware is unavailable."""
    def measure_ir(self, position: CylindricalPosition, order_id: str = "NA") -> None:
        logger.info(f"[MOCK] Measured {position}, ID={order_id}")
        time.sleep(1.0) # Simulate sweep duration

class AudioFactory:
    """Parses config files and instantiates the Audio engine."""
    
    @staticmethod
    def _get_required_config(config: configparser.ConfigParser, section: str, key: str, type_func):
        if not config.has_option(section, key):
            raise KeyError(f"Missing required config: [{section}] {key}")
        # Strip whitespace/comments just in case
        val = config.get(section, key).split('#')[0].split(';')[0].strip()
        return type_func(val)

    @staticmethod
    def create(config_file: str, audio_section: str = 'audio') -> IAudio:
        """
        Creates Audio instance.
        """
        # FIX: Tell parser to treat ';' and '#' as comments
        config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
        config.read(config_file)
        
        if not config.has_section(audio_section):
             raise KeyError(f"Config file missing [{audio_section}] section")

        if config.getboolean(audio_section, 'mock', fallback=False):
            return AudioMock()

        sweep_section = 'sweep'
        if not config.has_section(sweep_section):
             raise KeyError(f"Config file missing [{sweep_section}] section")

        def parse_optional_float(s):
            return None if s.lower() == "none" else float(s)

        return Audio(
            # --- HARDWARE SETTINGS (from [audio]) ---
            device_in_id=AudioFactory._get_required_config(config, audio_section, 'IN_DEV', int),
            device_out_id=AudioFactory._get_required_config(config, audio_section, 'OUT_DEV', int),
            ch_in_mic=AudioFactory._get_required_config(config, audio_section, 'IN_CH_MIC', int),
            ch_in_loop=AudioFactory._get_required_config(config, audio_section, 'IN_CH_LOOP', int),
            ch_out_spkr=AudioFactory._get_required_config(config, audio_section, 'OUT_CH_SPKR', int),
            ch_out_ref=AudioFactory._get_required_config(config, audio_section, 'OUT_CH_REF', int),
            fs=AudioFactory._get_required_config(config, audio_section, 'FS', int),
            blocksize=AudioFactory._get_required_config(config, audio_section, 'BLOCKSIZE', int),
            wasapi_exclusive=AudioFactory._get_required_config(config, audio_section, 'WASAPI_EXCLUSIVE', bool),
            
            # --- SIGNAL/SWEEP SETTINGS (from [sweep]) ---
            debug_saves=AudioFactory._get_required_config(config, sweep_section, 'DEBUG_SAVES', bool),
            
            sweep_dur_s=AudioFactory._get_required_config(config, sweep_section, 'SWEEP_DUR_S', float),
            sweep_level_dbfs=AudioFactory._get_required_config(config, sweep_section, 'SWEEP_LEVEL_DBFS', float),
            num_sweeps=AudioFactory._get_required_config(config, sweep_section, 'NUM_SWEEPS', int),
            
            align_to_first_marker=AudioFactory._get_required_config(config, sweep_section, 'ALIGN_TO_FIRST_MARKER', bool),
            
            pre_sil_ms=AudioFactory._get_required_config(config, sweep_section, 'PRE_SIL_MS', float),
            post_sil_ms=AudioFactory._get_required_config(config, sweep_section, 'POST_SIL_MS', float),
            mic_tail_taper_ms=AudioFactory._get_required_config(config, sweep_section, 'MIC_TAIL_TAPER_MS', float),
            
            # --- PROTECTION FILTER (from [sweep]) ---
            protect_hpf_hz=AudioFactory._get_required_config(config, sweep_section, 'PROTECT_HPF_HZ', parse_optional_float),
            protect_hpf_order=AudioFactory._get_required_config(config, sweep_section, 'PROTECT_HPF_ORDER', int),
            protect_hpf_phase=AudioFactory._get_required_config(config, sweep_section, 'PROTECT_HPF_PHASE', str),

            # --- TEST PARAMS ---
            h2_test_db=AudioFactory._get_required_config(config, sweep_section, 'H2_TEST_DB', parse_optional_float),
            h3_test_db=AudioFactory._get_required_config(config, sweep_section, 'H3_TEST_DB', parse_optional_float),
        )

if __name__ == "__main__":
    # Helper to list devices if run directly
    print("\nAvailable Audio Devices:")
    print(sd.query_devices())