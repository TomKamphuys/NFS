"""Convert recorded sweeps to impulse responses without opening audio hardware."""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import scipy.signal
import scipy.fft
from loguru import logger

from .utils.dsp import DSPUtils
from .audio_signals import (ProtectionFilter, _full_inverse, SINE_RMS_FROM_PEAK,
                            _LINEAR_IR_ORIGIN_SAMPLE, _EXPORT_FADE_FRACTION)

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


class CaptureAlignmentError(RuntimeError):
    """A completed capture could not be aligned to its playback markers."""

    def __init__(self, message: str, capture_context: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.capture_context = capture_context


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
            loopback_peak = float(np.max(np.abs(x))) if len(x) else 0.0
            loopback_rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0
            logger.warning(
                f"No Barker marker candidates in search range [{search_start}, {search_end}]; "
                f"recording_samples={len(x)}, marker_samples={len(ref)}, "
                f"loopback_peak={loopback_peak:.6g}, loopback_rms={loopback_rms:.6g}")
            raise CaptureAlignmentError("No Barker marker candidates in the expected search range")
        i = int(np.argmax(corr_sel))
        # Preserve the old raw-correlation return value for callers, while
        # using the normalized score to decide whether the marker is credible.
        peak_val = float(corr[m][i])
        peak_score = float(corr_sel[i])
        if normalize and peak_score < 0.5:
            loopback_peak = float(np.max(np.abs(x))) if len(x) else 0.0
            loopback_rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0
            logger.warning(
                f"No reliable Barker marker: normalized_correlation={peak_score:.3f} "
                f"(required >= 0.500), candidate_sample={int(lags_sel[i])}, "
                f"search_range=[{search_start}, {search_end}], "
                f"recording_samples={len(x)}, marker_samples={len(ref)}, "
                f"loopback_peak={loopback_peak:.6g}, loopback_rms={loopback_rms:.6g}")
            raise CaptureAlignmentError(
                f"No reliable Barker marker found (normalised correlation {peak_score:.3f})")

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
                raise CaptureAlignmentError(
                    f"Capture ended before sweep {i + 1} and its tail were fully recorded")

        if not mic_slices:
            raise CaptureAlignmentError("No valid sweeps captured (Alignment failed).")

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


class SweepToIR:
    """Hardware-free alignment, repeat averaging, deconvolution and IR metadata.

    Inputs are captured arrays and their digital references. No device, retry,
    detector or filesystem operations belong in this component.
    """

    def __init__(self, hw, cap, sweep_gen, alignment_engine, deconv_engine,
                 harmonic_injector=None, protection_filter=None):
        self.hw, self.cap = hw, cap
        self.sweep_gen = sweep_gen
        self.alignment_engine, self.deconv_engine = alignment_engine, deconv_engine
        self.harmonic_injector, self.protection_filter = harmonic_injector, protection_filter

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
        failure_context = {
            "capture_metadata": dict(timing),
            "raw_recordings": (rec_loop, rec_mic),
            "digital_reference": {
                "fundamental": prepared['fundamental'],
                "inverse": prepared['inverse'],
                "speaker_sweep": prepared['speaker_sweep'],
                "loopback_sweep": prepared['reference_sweep'],
                "marker": prepared['marker'],
                "transmitted_speaker": prepared['tx_speaker'],
                "transmitted_loopback": prepared['tx_loop'],
            },
        }
        try:
            avg_mic, avg_loop, mic_slices, psr = self.alignment_engine.sync_and_average(
                rec_mic, rec_loop, prepared['marker'], timing['pre_samples'],
                timing['slot_samples'], timing['sweep_samples'],
                sweep_offset_samples=timing['sweep_offset_samples'],
                capture_tail_samples=timing['capture_tail_samples'],
            )
        except CaptureAlignmentError as exc:
            failure_metadata = failure_context["capture_metadata"]
            failure_metadata.update({
                "aligned_repeats": [dict(row) for row in self.alignment_engine.last_alignment],
                "raw_capture_channels": ["electrical_loopback", "microphone"],
                "hardware_config": dict(self.hw),
                "capture_config": dict(self.cap),
            })
            exc.capture_context = failure_context
            raise
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


    def _process_capture(self, result: Dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
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
        return ir_full, ir_linear


    def _sweep_metadata(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "inverse": result['capture_metadata'].get('inverse_method', 'matched_v1'),
            "inverse_origin_sample": result['capture_metadata'].get(
                'inverse_origin_sample', len(result['inv_sweep']) - 1),
            "loopback_correction": dict(self.deconv_engine.last_loopback_correction),
            "log_sweep_rate_s": self.sweep_gen.log_sweep_rate,
            "guard_samples": self.sweep_gen.guard_samples,
            "active_duration_s": self.sweep_gen.active_duration_s,
        }

