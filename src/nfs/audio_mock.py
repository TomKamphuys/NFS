"""Mock audio implementations for testing without hardware.

- MockInterfaceAudio: Digital twin simulating hardware latency, FIR filters, HPF, and dropout injection.
- AudioMock: Minimal stub returning zeros for unit tests.
"""

from typing import Any, Callable, Dict, Optional

import numpy as np
import scipy.signal

from loguru import logger

from .audio import Audio, IAudio
from .audio_capture import reset_audio_meter_state
from .audio_signals import (HarmonicInjector, MarkerGenerator, ProtectionFilter,
                            SweepGenerator)
from .sweep_to_ir import AlignmentEngine, DeconvolutionEngine
from .datatypes import CylindricalPosition
from .naming import IRNamingConvention, NamingConventionFactory
from .debug_artifacts import DebugArtifactHandler


class MockInterfaceAudio(Audio):
    """Digital Twin loopback simulating hardware latency and filters."""

    def __init__(self,
                 hw_config: Dict[str, Any],
                 capture_config: Dict[str, Any],
                 sweep_gen: SweepGenerator,
                 marker_gen: MarkerGenerator,
                 alignment_engine: AlignmentEngine,
                 deconv_engine: DeconvolutionEngine,
                 naming_convention: IRNamingConvention,
                 debug_handler: DebugArtifactHandler,
                 harmonic_injector: Optional[HarmonicInjector] = None,
                 protection_filter: Optional[ProtectionFilter] = None,
                 position_callback: Optional[Callable[[], CylindricalPosition]] = None,
                 speaker_model: Optional[Any] = None):
        super().__init__(
            hw_config=hw_config,
            capture_config=capture_config,
            sweep_gen=sweep_gen,
            marker_gen=marker_gen,
            alignment_engine=alignment_engine,
            deconv_engine=deconv_engine,
            naming_convention=naming_convention,
            debug_handler=debug_handler,
            harmonic_injector=harmonic_injector,
            protection_filter=protection_filter,
        )
        self._position_callback = position_callback
        self._speaker_model = speaker_model

        # Dropout simulation counters — for testing the retry system.
        # Every 10th capture triggers a transient dropout (retry then succeed).
        # Every 20th capture triggers a persistent dropout (all retries exhausted).
        self._capture_count: int = 0
        self._dropout_persistent: bool = False
        self._persistent_fail_count: int = 0

    def set_position_callback(self, callback: Optional[Callable[[], CylindricalPosition]]) -> None:
        """Update the position callback at runtime before each capture."""
        self._position_callback = callback

    def _inject_dropout_if_needed(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Inject stream underflow into the result to simulate a mid-sweep dropout.

        Called after raw acquisition in each _run_sweep to determine if this capture
        should fail.  Every 10th capture triggers a one-time retry; every 20th
        capture fails permanently (all retries will be exhausted).
        """
        self._capture_count += 1
        n = self._capture_count

        def _inject_underflow():
            timing = result.get('capture_metadata', {})
            timing['stream_status'] = [
                {"sample": 0, "status": "InputUnderflow"}
            ]
            result['capture_metadata'] = timing

        # Every 20th point: persistent failure (all retries exhausted)
        if n % 20 == 0:
            self._dropout_persistent = True
            self._persistent_fail_count = 0
            _inject_underflow()
            logger.warning(
                f"[MOCK-INTERFACE] Simulating persistent dropout at capture #{n} "
                f"(will exhaust all retries)")
            return result
        # Persistent dropout retry attempts (following a 20th-point trigger)
        elif self._dropout_persistent:
            self._persistent_fail_count += 1
            # After 3 consecutive failures (matching max_capture_retries = 3), clear the flag
            if self._persistent_fail_count >= 3:
                self._dropout_persistent = False
                self._persistent_fail_count = 0
                logger.info(
                    f"[MOCK-INTERFACE] Clearing persistent dropout flag after capture #{n}")
                return result
            _inject_underflow()
            logger.warning(
                f"[MOCK-INTERFACE] Simulating persistent dropout at capture #{n} "
                f"(retry attempt, still failing)")
            return result
        # Every 10th point (not 20th): single retry then success
        elif n % 10 == 0:
            _inject_underflow()
            logger.warning(
                f"[MOCK-INTERFACE] Simulating transient dropout at capture #{n} "
                f"(will retry and succeed)")
            return result

        return result

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

        # Apply spatial speaker model if configured
        if self._speaker_model is not None and self._position_callback is not None:
            pos = self._position_callback()
            rec_mic = self._speaker_model.apply_to_signal(tx_sweep, self.hw['fs'], pos)
        else:
            rec_mic = tx_sweep.copy()

        logger.info("► Loopback Mode: Applying CS4272 FIR, 15Hz HPF, and 20ms delay.")
        rec_mic = apply_hardware_sim(rec_mic)
        rec_loop = apply_hardware_sim(tx_ref)
        capture_margin = int(0.020 * fs) + (len(fir_taps) - 1) // 2
        rec_mic = np.pad(rec_mic, (0, capture_margin))
        rec_loop = np.pad(rec_loop, (0, capture_margin))
        prepared['timing']['total_samples'] = len(rec_loop)

        result = self._raw_capture(prepared, rec_mic, rec_loop)

        # Simulate mid-sweep dropouts for testing the retry system.
        # Every 10th capture: transient dropout (retry succeeds).
        # Every 20th capture: persistent dropout (all retries exhausted).
        result = self._inject_dropout_if_needed(result)
        return result

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


class AudioMock(IAudio):
    """Simulation class for when hardware is unavailable."""

    def __init__(self):
        self.naming_convention: IRNamingConvention = NamingConventionFactory.create('dimitri')

    def capture_ir(self, ir_path, dist_path) -> dict[str, Any]:
        logger.debug(f"[MOCK] Captured IR → {ir_path.name}")
        return {"fs": 48000, "ir_linear": np.zeros(1000), "ir_full": np.zeros(1000), "metrics": {}}

    def preview_ir(self) -> dict[str, Any]:
        logger.debug("[MOCK] Preview IR")
        return {"fs": 48000, "ir_linear": np.zeros(1000), "ir_full": np.zeros(1000), "metrics": {}}

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
