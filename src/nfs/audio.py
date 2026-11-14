"""Audio measurement coordinator.

- audio_signals defines sweeps, matched inverses, markers and playback filters.
- audio_capture prepares playback and owns raw acquisition, tones and metering.
- sweep_to_ir aligns/averages recordings, deconvolves and calculates IR metrics.
- capture_detection assesses captures; this module owns retries and saving.

Legacy imports and private processing wrappers remain available for callers.
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
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Callable, Optional, Dict, Any, List, Tuple
from nfs import registry
from nfs.capture_detection import (CaptureContext, CaptureDetector, DEFAULT_DETECTORS,
                                   find_issue)

import numpy as np
import scipy.signal
import scipy.fft
import soundfile as sf

from loguru import logger

from .datatypes import CylindricalPosition
from .utils.dsp import DSPUtils

from . import _audio_backend as sd
from .naming import IRNamingConvention, NamingConventionFactory
from .debug_artifacts import DebugArtifactHandler, DebugArtifactFactory

# Block size is hardcoded to 0 (auto) — let the audio driver decide.
BLOCKSIZE = 0


from .audio_signals import (MarkerGenerator, SweepGenerator, HarmonicInjector,
                            ProtectionFilter, _full_inverse, SINE_RMS_FROM_PEAK,
                            _LINEAR_IR_ORIGIN_SAMPLE, _EXPORT_FADE_FRACTION)
from .sweep_to_ir import (SweepToIR, DSPVerificationTool, CaptureAlignmentError,
                          AlignmentEngine, DeconvolutionEngine)
from .audio_capture import AudioCapture
from .audio_capture import (_set_realtime_priority,
                            _asio_thread_context,
                            _stream_channel_settings,
                            _channel_meter,
                            _two_channel_meters,
                            _a_weighting_filter,
                            _c_weighting_filter,
                            _two_channel_weighted_meters,
                            _two_channel_a_weighted_meters,
                            _two_channel_c_weighted_meters,
                            _role_frames,
                            _AudioMeterWorker,
                            update_audio_meter_state,
                            reset_audio_meter_state,
                            get_audio_meter_state,
                            clean_device_name,
                            get_devices_and_channels,
                            get_supported_sample_rates,
                            find_device_id_by_name,
                            resolve_device_id_by_saved_identity)


class IAudio(ABC):
    @abstractmethod
    def capture_ir(self, ir_path: Path, dist_path: Path) -> dict[str, Any]:
        """Capture, process, and save IR to the given paths."""
        pass

    def measure_ir(self, position: CylindricalPosition, prefix: str | None = None,
                   save: bool = True) -> dict[str, Any]:
        session_dir = getattr(self, 'rec_dir', Path('./Recordings'))
        ir_path = self.naming_convention.build_ir_path(position, session_dir, prefix)
        dist_path = self.naming_convention.build_dist_path(position, session_dir, prefix)
        return self.capture_ir(ir_path, dist_path)

    @abstractmethod
    def preview_ir(self) -> dict[str, Any]:
        """Capture and process IR without saving (for test/preview)."""
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
                 naming_convention: Optional[IRNamingConvention] = None,
                 debug_handler: Optional[DebugArtifactHandler] = None,
                 harmonic_injector: Optional[HarmonicInjector] = None,
                 protection_filter: Optional[ProtectionFilter] = None,
                 hardware_io=None,
                 detectors: tuple[CaptureDetector, ...] | None = None):

        self.hw = hw_config
        self.cap = capture_config
        self._hardware_io = hardware_io
        self.detectors = tuple(DEFAULT_DETECTORS if detectors is None else detectors)
        self.sweep_gen = sweep_gen
        self.marker_gen = marker_gen
        self.alignment_engine = alignment_engine
        self.deconv_engine = deconv_engine
        self.naming_convention = naming_convention or NamingConventionFactory.create(
            self.cap.get('naming_convention', 'dimitri'))
        self.debug_handler = debug_handler or DebugArtifactFactory.create(
            self.cap.get('debug_saves', True), self.hw)
        self.harmonic_injector = harmonic_injector
        self.protection_filter = protection_filter
        self.verifier = DSPVerificationTool(self.hw['fs'])
        self.capture_engine = AudioCapture(hw_config, capture_config, sweep_gen, marker_gen,
                                           harmonic_injector, protection_filter, hardware_io)
        self.ir_processor = SweepToIR(hw_config, capture_config, sweep_gen, alignment_engine,
                                     deconv_engine, harmonic_injector, protection_filter)


        # Directories
        self.rec_dir = Path("./Recordings")
        self.dist_dir = self.rec_dir / "Distortion"
        self.debug_dir = self.rec_dir / "debug"
        # The app starts a fresh session for a scan or standalone measurement.
        # Direct API users also have a default session until they set a directory.
        self._reference_session_id = uuid.uuid4().hex

        self._log_config()

        # Elevate process priority on Windows to reduce audio buffer underruns.
        # This is a preventive measure alongside the retry-based dropout detection.
        _set_realtime_priority()

    def _capture_component(self):
        # Preserve the existing configurable attributes while the components own
        # implementation and stream state. Config dictionaries are shared.
        for name in ('hw', 'cap', 'sweep_gen', 'marker_gen', 'harmonic_injector',
                     'protection_filter', '_hardware_io'):
            setattr(self.capture_engine, name, getattr(self, name))
        return self.capture_engine

    def _ir_component(self):
        for name in ('hw', 'cap', 'sweep_gen', 'alignment_engine', 'deconv_engine',
                     'harmonic_injector', 'protection_filter'):
            setattr(self.ir_processor, name, getattr(self, name))
        return self.ir_processor

    def _ensure_directories(self) -> None:
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        self.dist_dir.mkdir(parents=True, exist_ok=True)

    def set_session_directory(self, session_path: Path) -> None:
        """Begin a new acquisition session in the supplied recording directory.

        Called once before a scan, or once per standalone measurement, even
        when two acquisitions use the same directory. Pausing a scan does not
        call this method, so its references remain shared after resuming.
        """
        self._reference_session_id = uuid.uuid4().hex
        self.rec_dir = Path(session_path)
        self.dist_dir = self.rec_dir / "Distortion"
        self.debug_dir = self.rec_dir / "debug"
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

    def _save_failed_capture(self, capture: Dict[str, Any], measurement_label: str,
                             attempt: int, attempt_limit: int, reason: str,
                             save: bool, exception: Optional[BaseException] = None,
                             metrics: Optional[Dict[str, Any]] = None) -> Optional[Path]:
        """Archive a rejected completed capture without affecting its retry."""
        if not save or not self.cap.get('debug_saves', True):
            return None

        try:
            failed_root = self.rec_dir / 'failed'
            failed_root.mkdir(parents=True, exist_ok=True)
            archive_dir = None
            for _ in range(10):
                candidate = failed_root / f"{measurement_label}_{uuid.uuid4().hex}"
                try:
                    candidate.mkdir()
                except FileExistsError:
                    continue
                archive_dir = candidate
                break
            if archive_dir is None:
                raise FileExistsError("Could not allocate a unique failed-capture directory")

            capture_filename = f'attempt_{attempt:02d}_capture.wav'
            failure_filename = f'attempt_{attempt:02d}_failure.json'
            capture_path = archive_dir / capture_filename
            failure_path = archive_dir / failure_filename
            reference = self._save_shared_reference(
                capture['digital_reference'], capture.get('acquisition_metadata'))
            raw_channels = capture['raw_recordings']
            self._save_wav_with_metadata(
                capture_path, np.column_stack(raw_channels), capture_filename, subtype='FLOAT')

            capture_metadata = capture.get('capture_metadata', {})
            aligned_repeats = capture_metadata.get('aligned_repeats', []) or []
            detected_positions = []
            for repeat_index, row in enumerate(aligned_repeats, start=1):
                detected = {"repeat": repeat_index}
                for key in ('marker_start_sample', 'sweep_start_sample'):
                    if key in row:
                        detected[key] = int(row[key])
                detected_positions.append(detected)

            psr = metrics.get('psr') if metrics is not None else capture.get('psr')
            reference_psr = (capture.get('psr_reference') if metrics is None
                             else metrics.get('psr_reference'))
            observed_timing = {
                key: capture_metadata[key]
                for key in ('actual_sample_rate', 'stream_latency_s', 'stream_status')
                if key in capture_metadata
            }
            observed_timing['recorded_samples'] = {
                'electrical_loopback': len(raw_channels[0]),
                'microphone': len(raw_channels[1]),
            }
            document = {
                'schema_version': 1,
                'measurement_label': measurement_label,
                'attempt': attempt,
                'attempt_limit': attempt_limit,
                'reason': reason,
                'exception': ({'type': type(exception).__name__, 'message': str(exception)}
                              if exception is not None else None),
                'sample_rate': self.hw['fs'],
                'channel_mapping': {
                    'recording_order': ['electrical_loopback', 'microphone'],
                    'input_channels': {
                        'electrical_loopback': self.hw['ch_in_loop'],
                        'microphone': self.hw['ch_in_mic'],
                    },
                    'output_channels': {
                        'speaker_sweep': self.hw['ch_out_spkr'],
                        'electrical_reference': self.hw['ch_out_ref'],
                    },
                },
                'hardware_settings': dict(self.hw),
                'capture_settings': dict(self.cap),
                'planned_playback_marker_positions_samples':
                    capture_metadata.get('playback_marker_starts'),
                'observed_stream': observed_timing,
                'detected_alignment_positions': detected_positions,
                'quality': {
                    'psr': float(psr) if psr is not None else None,
                    'reference_psr': float(reference_psr) if reference_psr is not None else None,
                    'retry_psr_threshold': self.cap.get('retry_psr_threshold', 3.0),
                },
                'artifacts': {
                    'capture_wav': capture_filename,
                    'shared_reference_schema': reference['reference_archive_schema'],
                    'shared_reference_session_id': reference['reference_session_id'],
                    'shared_reference_id': reference['reference_id'],
                    'shared_reference_path_relative_to_failure_json':
                        (Path('..') / '..' / 'debug' / reference['reference_file']).as_posix(),
                },
            }
            json_contents = json.dumps(document, indent=2, allow_nan=False)
            temporary_path = failure_path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
            try:
                with temporary_path.open('x', encoding='utf-8') as handle:
                    handle.write(json_contents)
                temporary_path.replace(failure_path)
            finally:
                temporary_path.unlink(missing_ok=True)

            logger.info(f"Saved failed capture archive: {capture_path}")
            logger.info(f"Saved failed capture context: {failure_path}")
            logger.info(f"Saved failed capture reference: {self.debug_dir / reference['reference_file']}")
            return failure_path
        except Exception:
            logger.exception(
                f"Could not save failed capture for {measurement_label}, attempt "
                f"{attempt}/{attempt_limit}; continuing capture retries")
            return None

    def _log_config(self) -> None:
        """Log the current hardware and capture configuration."""
        logger.info(
            f"Audio Config: FS={self.hw['fs']}, Sweeps={self.cap['num_sweeps']}, Dur={self.cap['sweep_dur_s']}s")
        logger.info(f"Devices: In={self.hw['dev_in']}, Out={self.hw['dev_out']}")

    def _get_api_name(self, dev_index: int) -> str:
        return self._capture_component()._get_api_name(dev_index)

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
        return self._capture_component()._prepare_sweep()

    def _finish_sweep(self, prepared: Dict[str, Any], rec_mic: np.ndarray,
                      rec_loop: np.ndarray) -> Dict[str, Any]:
        return self._ir_component()._finish_sweep(prepared, rec_mic, rec_loop)

    def _run_sweep(self) -> Dict[str, Any]:
        """
        Executes the playback and recording of the composite signal.
        Handles stream synchronization, multiple sweep averaging, and loopback alignment.
        """
        prepared = self._prepare_sweep()
        if self._hardware_io is not None:
            rec_mic, rec_loop, observed = self._hardware_io.capture(
                self.hw, prepared['tx_speaker'], prepared['tx_loop'])
            prepared['timing'].update(observed)
        else:
            rec_mic, rec_loop = self._capture_buffers(prepared)
        return self._finish_sweep(prepared, rec_mic, rec_loop)

    def _capture_buffers(self, prepared: Dict[str, Any], recordings=None):
        return self._capture_component()._capture_buffers(prepared, recordings)

    def _process_capture(self, result: Dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
        return self._ir_component()._process_capture(result)

    def _sweep_metadata(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return self._ir_component()._sweep_metadata(result)

    # ─────────────────────────────────────────────────────────────────────────────
    #  Capture orchestration and retries
    # ─────────────────────────────────────────────────────────────────────────────

    def capture_ir(self, ir_path: Path, dist_path: Path, save: bool = True,
                   include_debug_artifacts: bool = True) -> dict[str, Any]:
        """
        Capture, process, and save IR to the given paths.

        The caller is responsible for constructing the output paths using the
        naming convention. This method focuses on audio capture and processing.

        Uses a retry loop with layered dropout detection:
        - Layer 1: Stream health check (underflow/overflow flags)
        - Layer 2: Silence gap detection in loopback and microphone recordings
        - Layer 3: PSR and THD quality gates after deconvolution
        """
        if save:
            ir_path.parent.mkdir(parents=True, exist_ok=True)
            dist_path.parent.mkdir(parents=True, exist_ok=True)

        base_label = ir_path.stem
        if base_label.endswith("_ir"):
            base_label = base_label[:-3]  # strip _ir suffix for debug labels

        max_retries = self.cap.get('max_capture_retries', 3)
        last_rejection_reason = None

        for attempt in range(max_retries):
            # 1. Capture Raw Data (Run Sweeps)
            try:
                result = self._run_sweep()
            except CaptureAlignmentError as exc:
                last_rejection_reason = f"alignment failure: {exc}"
                logger.warning(
                    f"Capture rejected for {ir_path.name}: {last_rejection_reason}, "
                    f"retry {attempt + 1}/{max_retries}")
                if exc.capture_context is not None:
                    self._save_failed_capture(
                        exc.capture_context, base_label, attempt + 1, max_retries,
                        last_rejection_reason, save, exception=exc)
                continue
            except Exception:
                logger.exception(
                    f"Sweep capture failed for {ir_path.name}, attempt {attempt + 1}/{max_retries}; "
                    f"exception will be propagated")
                raise

            issue = find_issue(self.detectors, "raw", CaptureContext(result, self.hw['fs'], self.cap))
            if issue is not None:
                last_rejection_reason = issue.reason
                logger.warning(
                    f"Capture rejected for {ir_path.name}: {issue.reason}, "
                    f"retry {attempt + 1}/{max_retries}")
                self._save_failed_capture(
                    result, base_label, attempt + 1, max_retries, last_rejection_reason, save)
                continue

            # Deconvolution + quality gate
            ir_full, ir_linear = self._process_capture(result)

            # 4. DSP Verification
            metrics = self.verifier.calculate_metrics(
                ir_full, ir_linear, result.get("psr", 0.0), result.get("psr_reference"),
                linear_start_sample=result['capture_metadata']['sweep_samples'] - (_LINEAR_IR_ORIGIN_SAMPLE + 1),
            )

            issue = find_issue(
                self.detectors, "metrics", CaptureContext(
                    result, self.hw['fs'], self.cap, metrics, ir_full, ir_linear))
            if issue is not None:
                last_rejection_reason = issue.reason
                logger.warning(
                    f"Capture rejected for {ir_path.name}: {issue.reason}, "
                    f"retry {attempt + 1}/{max_retries}")
                self._save_failed_capture(
                    result, base_label, attempt + 1, max_retries, last_rejection_reason, save,
                    metrics=metrics)
                continue

            # Success: proceed to save and return
            logger.info(
                f"DSP Metrics: SNR={metrics['snr_db']:.1f}dB, THD={metrics['thd_pct']:.2f}%, "
                f"marker clarity={metrics['psr_db']:.1f}dB / "
                f"{metrics.get('psr_reference_db', 0.0):.1f}dB reference "
                f"({metrics.get('alignment_score_10', 0.0):.1f}/10)")

            warnings = self.verifier.verify(metrics)
            for w in warnings:
                logger.warning(f"VERIFICATION FAILURE: {w}")

            # 2. Debug Saves (only for the successful attempt)
            if save and include_debug_artifacts:
                self.debug_handler.save_wav(result["rx_mic_conditioned"],
                                            f"{base_label}_mic_conditioned", self.rec_dir)
                self.debug_handler.save_wav(result["rx_loop_aligned"],
                                            f"{base_label}_loop_aligned", self.rec_dir)
                for i, slice_data in enumerate(result["debug_mic_slices"]):
                    self.debug_handler.save_wav(slice_data,
                                                f"{base_label}_sweep{i + 1:02d}", self.rec_dir)

            # Save raw input and shared references when debug capture is enabled.
            if save and self.cap.get('debug_saves', True):
                logger.info("Saving raw capture and shared reference...")
                result['capture_metadata'].update(
                    self._save_shared_reference(result['digital_reference'],
                                                result['acquisition_metadata'])
                )
                self.debug_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{base_label}_capture.wav"
                self._save_wav_with_metadata(
                    self.debug_dir / filename, np.column_stack(result['raw_recordings']),
                    filename, subtype='FLOAT',
                )

            if save:
                self._save_wav_with_metadata(ir_path, ir_linear, ir_path.name, subtype='FLOAT')
                logger.info(f"Saved Linear IR: {ir_path.name}")

                self._save_wav_with_metadata(dist_path, ir_full, dist_path.name, subtype='FLOAT')
                logger.info(f"Saved Distortion IR: {dist_path.name}")

                self.debug_handler.save_metrics(metrics, base_label, self.rec_dir)

            return {
                "fs": self.hw["fs"],
                "ir_linear": ir_linear,
                "ir_full": ir_full,
                "sweep_metadata": self._sweep_metadata(result),
                "metrics": metrics,
                "capture_metadata": result['capture_metadata'],
                "name": ir_path.name,
                "saved": save,
                "retries": attempt,
            }

        # All attempts exhausted — return a failure result without saving files
        failure_reason = last_rejection_reason or "no capture attempts were configured"
        logger.error(
            f"Measurement failed after {max_retries} attempts: {failure_reason}, "
            f"skipping {ir_path.name}")
        return {
            "fs": self.hw["fs"],
            "ir_linear": None,
            "ir_full": None,
            "sweep_metadata": {},
            "metrics": {},
            "capture_metadata": {},
            "name": ir_path.name,
            "saved": False,
            "error": f"Measurement failed after {max_retries} attempts: {failure_reason}",
        }

    def measure_ir(self, position: CylindricalPosition, prefix: str | None = None,
                   save: bool = True) -> dict[str, Any]:
        ir_path = self.naming_convention.build_ir_path(position, self.rec_dir, prefix)
        dist_path = self.naming_convention.build_dist_path(position, self.rec_dir, prefix)
        return self.capture_ir(ir_path, dist_path, save=save, include_debug_artifacts=False)

    def preview_ir(self) -> dict[str, Any]:
        """
        Capture and process IR without saving (for test/preview).

        Uses the same retry loop with layered dropout detection as capture_ir.
        """
        max_retries = self.cap.get('max_capture_retries', 3)

        for attempt in range(max_retries):
            # 1. Capture Raw Data (Run Sweeps)
            result = self._run_sweep()

            issue = find_issue(self.detectors, "raw", CaptureContext(result, self.hw['fs'], self.cap))
            if issue is not None:
                logger.warning(
                    f"Preview: Capture rejected ({issue.reason}), "
                    f"retry {attempt + 1}/{max_retries}")
                continue

            # Deconvolution + quality gate
            ir_full, ir_linear = self._process_capture(result)

            # 3. DSP Verification
            metrics = self.verifier.calculate_metrics(ir_full, ir_linear, result.get("psr", 0.0))

            issue = find_issue(
                self.detectors, "metrics", CaptureContext(
                    result, self.hw['fs'], self.cap, metrics, ir_full, ir_linear))
            if issue is not None:
                logger.warning(
                    f"Preview: Capture rejected ({issue.reason}), "
                    f"retry {attempt + 1}/{max_retries}")
                continue

            # Success
            logger.info(
                f"DSP Metrics: SNR={metrics['snr_db']:.1f}dB, THD={metrics['thd_pct']:.2f}%, "
                f"marker clarity={metrics['psr_db']:.1f}dB")

            warnings = self.verifier.verify(metrics)
            for w in warnings:
                logger.warning(f"VERIFICATION FAILURE: {w}")
            return {
                "fs": self.hw["fs"],
                "ir_linear": ir_linear,
                "ir_full": ir_full,
                "sweep_metadata": self._sweep_metadata(result),
                "metrics": metrics,
                "capture_metadata": result['capture_metadata'],
                "saved": False,
            }

        # All retries exhausted
        logger.error(
            f"Preview measurement failed after {max_retries} retries due to dropout")
        return {
            "fs": self.hw["fs"],
            "ir_linear": None,
            "ir_full": None,
            "sweep_metadata": {},
            "metrics": {},
            "capture_metadata": {},
            "saved": False,
            "error": f"Preview measurement failed after {max_retries} retries due to dropout",
        }

    def play_sine(self, frequency: float, level_dbfs: float, duration_s: Optional[float] = 1.0) -> None:
        return self._capture_component().play_sine(frequency, level_dbfs, duration_s)

    def update_sine_level(self, level_dbfs: float) -> None:
        return self._capture_component().update_sine_level(level_dbfs)

    def stop_sine(self) -> None:
        return self._capture_component().stop_sine()


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

        Called after _finish_sweep in each _run_sweep to determine if this capture
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

        result = self._finish_sweep(prepared, rec_mic, rec_loop)

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


# ─────────────────────────────────────────────────────────────────────────────
#  FACTORY
# ─────────────────────────────────────────────────────────────────────────────

class AudioMock(IAudio):
    """Simulation class for when hardware is unavailable."""

    def __init__(self):
        self.naming_convention: IRNamingConvention = NamingConventionFactory.create('dimitri')

    def capture_ir(self, ir_path: Path, dist_path: Path) -> dict[str, Any]:
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

        hardware_io = None
        if mode == 'mock_interface':
            dev_in = configured_in
            dev_out = configured_out
        else:
            from .audio_process import get_audio_process
            hardware_io = get_audio_process()
            catalog = hardware_io.get_devices_and_channels()
            dev_in = resolve_device_id_by_saved_identity(
                saved_in_name,
                saved_in_api,
                role_label="input",
                require_input=True,
                catalog=catalog,
            )
            dev_out = resolve_device_id_by_saved_identity(
                saved_out_name,
                saved_out_api,
                role_label="output",
                require_output=True,
                catalog=catalog,
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
            'blocksize': BLOCKSIZE,
            'wasapi_exclusive': AudioFactory._get_required_config(config, audio_section, 'wasapi_exclusive', bool),
        }

        naming_convention_str = config.get(sweep_section, 'naming_convention', fallback='dimitri').strip()
        naming_convention: IRNamingConvention = NamingConventionFactory.create(naming_convention_str)

        debug_saves = AudioFactory._get_required_config(config, sweep_section, 'debug_saves', bool)
        debug_handler: DebugArtifactHandler = DebugArtifactFactory.create(debug_saves, hw_config)

        cap_config = {
            'loopback_correction': config.getboolean(sweep_section, 'loopback_correction', fallback=True),
            'naming_convention': naming_convention_str,
            'debug_saves': debug_saves,
            'sweep_dur_s': sweep_dur_s,
            'sweep_level_dbfs': sweep_level_dbfs,
            'num_sweeps': AudioFactory._get_required_config(config, sweep_section, 'num_sweeps', int),
            'pre_sil_ms': AudioFactory._get_required_config(config, sweep_section, 'pre_sil_ms', float),
            'post_sil_ms': AudioFactory._get_required_config(config, sweep_section, 'post_sil_ms', float),
            'marker_gap_ms': 20.0,  # Fixed marker settling gap; adjust here if needed.
            'max_capture_retries': config.getint(sweep_section, 'max_capture_retries', fallback=3),
            **{detector.enabled_key: config.getboolean(
                sweep_section, detector.enabled_key, fallback=True)
                for detector in DEFAULT_DETECTORS},
            'retry_psr_threshold': config.getfloat(sweep_section, 'retry_psr_threshold', fallback=3.0),
            'retry_thd_threshold': config.getfloat(sweep_section, 'retry_thd_threshold', fallback=10.0),
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
            'naming_convention': naming_convention,
            'debug_handler': debug_handler,
            'harmonic_injector': injector,
            'protection_filter': filter_engine
        }

        # Route to the correct class based on mode
        if mode == 'mock_interface':
            # Optionally enable spatial speaker model in mock mode
            spatial_model_str = config.get(sweep_section, 'spatial_model', fallback='false').strip().lower()
            if spatial_model_str in ('true', '1', 'yes', 'spherical_harmonic'):
                from .utils.spatial import SphericalHarmonicSpeakerModel
                kwargs['speaker_model'] = SphericalHarmonicSpeakerModel()
            elif spatial_model_str == 'discrete_piston':
                from .utils.spatial import DiscretePistonSpeakerModel
                kwargs['speaker_model'] = DiscretePistonSpeakerModel()
            return MockInterfaceAudio(**kwargs)

        return Audio(**kwargs, hardware_io=hardware_io)


if __name__ == "__main__":
    # Helper to list devices if run directly
    print("\nAvailable Audio Devices:")
    print(sd.query_devices())
