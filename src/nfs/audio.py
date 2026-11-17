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
from dataclasses import replace
from abc import ABC, abstractmethod
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Callable, Optional, Dict, Any, List, Tuple
from nfs import registry
from nfs.capture_detection import (CaptureContext, CaptureDetector, DEFAULT_DETECTORS,
                                   DetectorSession, DetectorPreparationCancelled,
                                   find_issue, prepare_detectors, archive_detector_state)
from nfs.capture_detection import DetectorEvent, DetectorDecision

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
    def set_capture_output_directory(self, path: Path) -> None:
        """Redirect a capture bundle without resetting the batch's detector reference."""
        self.rec_dir = Path(path)
        self.dist_dir = self.rec_dir / 'Distortion'
        self.debug_dir = self.rec_dir / 'debug'
        self._ensure_directories()

    def begin_measurement_set(self, should_continue=None) -> None:
        """Prepare any detector state before the first measurement point."""

    def end_measurement_set(self) -> None:
        """Release detector state after a set finishes, fails or is stopped."""

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
        self.cap = dict(capture_config, debug_saves=True)
        self._hardware_io = hardware_io
        self.detectors = tuple(DEFAULT_DETECTORS if detectors is None else detectors)
        self._detector_state = {}
        self._measurement_set_active = False
        self._detector_observer = None
        self._detector_should_continue = None
        self._disabled_detectors = set()
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
        self.end_measurement_set()
        self._reference_session_id = uuid.uuid4().hex
        self.set_capture_output_directory(session_path)
        logger.debug(f"Audio session directory updated to: {session_path}")

    def begin_measurement_set(self, should_continue=None) -> None:
        self.end_measurement_set()
        self._detector_should_continue = should_continue

        def acquire(num_sweeps=None):
            if should_continue is not None and not should_continue():
                raise DetectorPreparationCancelled()
            original = self.cap
            try:
                if num_sweeps is not None:
                    self.cap = dict(original, num_sweeps=num_sweeps)
                result = self._run_sweep()
            finally:
                self.cap = original
                self._capture_component()  # Restore the shared capture settings, even on error.
            if should_continue is not None and not should_continue():
                raise DetectorPreparationCancelled()
            return result

        # Prepare into a temporary dictionary: an incomplete reference is never
        # made available to measurements if acquisition or validation fails.
        state = prepare_detectors(self.detectors, DetectorSession(
            self.hw['fs'], self.cap, acquire, self._notify_detector,
            self._detector_observer is not None, self._capture_duration_s(), self._disabled_detectors,
            single_capture_duration_s=self._capture_duration_s(num_sweeps=1)))
        self._detector_state = state
        self._measurement_set_active = True

    def end_measurement_set(self) -> None:
        self._detector_state = {}
        self._measurement_set_active = False
        self._disabled_detectors = set()
        self._detector_should_continue = None

    def set_detector_observer(self, observer) -> None:
        self._detector_observer = observer

    def _capture_duration_s(self, num_sweeps=None):
        repeats = self.cap.get('num_sweeps', 1) if num_sweeps is None else num_sweeps
        return (self.cap.get('pre_sil_ms', 0) / 1000
                + repeats * (getattr(self.marker_gen, 'dur_ms', 100) / 1000
                             + self.cap.get('marker_gap_ms', 20) / 1000
                             + self.cap.get('sweep_dur_s', 0)
                             + self.cap.get('post_sil_ms', 0) / 1000))

    def _notify_detector(self, event):
        if self._detector_observer is None:
            return
        if event.hold_seconds and event.decision is None:
            event = replace(event, decision=DetectorDecision())
        self._detector_observer(event)
        if event.decision is not None:
            choice = event.decision.wait(self._detector_should_continue)
            if choice == 'cancel' and event.kind not in ('preparation_failed', 'point_accepted', 'point_failed'):
                raise DetectorPreparationCancelled()

    def _notify_rejection(self, reason, attempt, limit, point, detector='alignment'):
        self._notify_detector(DetectorEvent(
            'attempt_rejected', 'Capture fault detection', f'Attempt {attempt} of {limit} rejected.',
            detector=detector, details=reason, point=point, attempt=attempt, max_attempts=limit))

    def _detector_context(self, result, metrics=None, ir_full=None, ir_linear=None):
        return CaptureContext(result, self.hw['fs'], self.cap, metrics, ir_full, ir_linear,
                              self._detector_state, self._measurement_set_active,
                              disabled_detectors=frozenset(self._disabled_detectors))

    def _save_shared_reference(self, arrays: Dict[str, np.ndarray],
                               acquisition: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """Save digital references and prepared detector state in the shared NPZ.

        Hash the samples, their names/shapes/dtypes and acquisition metadata. This means
        changed playback or inverse data cannot silently reuse an older file,
        while equal references from thousands of positions share one archive.
        """
        # A Unicode scalar is readable with allow_pickle=False. Never archive
        # Python objects or capture-specific alignment/processing results.
        metadata = dict(acquisition or {})
        metadata.update(schema="shared_reference_v2", sample_rate=self.hw['fs'])
        payload = dict(arrays)
        detector_arrays = archive_detector_state(self.detectors, self._detector_state)
        if payload.keys() & detector_arrays.keys():
            raise ValueError('Detector archive fields cannot replace digital reference fields')
        payload.update(detector_arrays)
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

    def _raw_capture(self, prepared, rec_mic, rec_loop) -> Dict[str, Any]:
        capture = self._capture_component()._raw_capture(prepared, rec_mic, rec_loop)
        if self._disabled_detectors:
            disabled = sorted(self._disabled_detectors)
            capture['capture_metadata']['disabled_detectors_for_scan'] = disabled
            capture['acquisition_metadata']['disabled_detectors_for_scan'] = disabled
        return capture

    def _align_capture(self, capture: Dict[str, Any]) -> Dict[str, Any]:
        return self._ir_component()._align_capture(capture)

    def _finish_sweep(self, prepared, rec_mic, rec_loop) -> Dict[str, Any]:
        """Prepare captured buffers for DSP (offline replay convenience)."""
        return self._align_capture(self._raw_capture(prepared, rec_mic, rec_loop))

    def _run_sweep(self) -> Dict[str, Any]:
        """
        Executes the playback and recording of the composite signal.
        Returns untouched raw recordings; detectors run before DSP alignment/averaging.
        """
        prepared = self._prepare_sweep()
        if self._hardware_io is not None:
            rec_mic, rec_loop, observed = self._hardware_io.capture(
                self.hw, prepared['tx_speaker'], prepared['tx_loop'])
            prepared['timing'].update(observed)
        else:
            rec_mic, rec_loop = self._capture_buffers(prepared)
        return self._raw_capture(prepared, rec_mic, rec_loop)

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
            if attempt:
                if self._detector_should_continue is not None and not self._detector_should_continue():
                    raise DetectorPreparationCancelled()
                self._notify_detector(DetectorEvent(
                    'retry_started', 'Repeating suspect capture', 'Capturing a replacement sweep sequence…',
                    details=last_rejection_reason or '', point=base_label,
                    attempt=attempt + 1, max_attempts=max_retries, duration_s=self._capture_duration_s()))
            # 1. Capture Raw Data (Run Sweeps)
            try:
                result = self._run_sweep()
                issue = find_issue(self.detectors, "raw", self._detector_context(result))
                if issue is not None:
                    last_rejection_reason = issue.reason
                    self._notify_rejection(issue.reason, attempt + 1, max_retries, base_label, issue.detector)
                    logger.warning(
                        f"Capture rejected for {ir_path.name}: {issue.reason}, "
                        f"retry {attempt + 1}/{max_retries}")
                    self._save_failed_capture(
                        result, base_label, attempt + 1, max_retries, last_rejection_reason, save)
                    continue

                if attempt:
                    self._notify_detector(DetectorEvent(
                        'checking', 'Checking replacement capture', 'Checking alignment and capture quality…',
                        point=base_label, attempt=attempt + 1, max_attempts=max_retries))
                result = self._align_capture(result)

            except CaptureAlignmentError as exc:
                last_rejection_reason = f"alignment failure: {exc}"
                self._notify_rejection(last_rejection_reason, attempt + 1, max_retries, base_label)
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

            # Deconvolution + quality gate
            ir_full, ir_linear = self._process_capture(result)

            # 4. DSP Verification
            metrics = self.verifier.calculate_metrics(
                ir_full, ir_linear, result.get("psr", 0.0), result.get("psr_reference"),
                linear_start_sample=result['capture_metadata']['sweep_samples'] - (_LINEAR_IR_ORIGIN_SAMPLE + 1),
            )

            issue = find_issue(
                self.detectors, "metrics", self._detector_context(result, metrics, ir_full, ir_linear))
            if issue is not None:
                last_rejection_reason = issue.reason
                self._notify_rejection(issue.reason, attempt + 1, max_retries, base_label, issue.detector)
                logger.warning(
                    f"Capture rejected for {ir_path.name}: {issue.reason}, "
                    f"retry {attempt + 1}/{max_retries}")
                self._save_failed_capture(
                    result, base_label, attempt + 1, max_retries, last_rejection_reason, save,
                    metrics=metrics)
                continue

            # Success: proceed to save and return
            if attempt:
                self._notify_detector(DetectorEvent(
                    'point_accepted', 'Capture accepted', f'Replacement accepted on attempt {attempt + 1} of {max_retries}.',
                    point=base_label, attempt=attempt + 1, max_attempts=max_retries, hold_seconds=1.0))
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
                sidecar = self.debug_dir / f"{base_label}_capture.json"
                temporary = sidecar.with_suffix(f'.{uuid.uuid4().hex}.tmp')
                try:
                    temporary.write_text(json.dumps(result['capture_metadata'], indent=2,
                                                    allow_nan=False), encoding='utf-8')
                    temporary.replace(sidecar)
                finally:
                    temporary.unlink(missing_ok=True)


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
        stop_scan = self.cap.get('stop_on_capture_failure', False)
        self._notify_detector(DetectorEvent(
            'point_failed', 'Capture rejected', f'All {max_retries} capture attempts were rejected.',
            details=f"{failure_reason}\n\n" + ('Stopping the scan.' if stop_scan else 'Skipping this point and continuing the scan.'),
            point=base_label, attempt=max_retries, max_attempts=max_retries, hold_seconds=1.0))
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
            "stop_scan": stop_scan,
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
            try:
                result = self._run_sweep()

                issue = find_issue(self.detectors, "raw", self._detector_context(result))
                if issue is not None:
                    logger.warning(
                        f"Preview: Capture rejected ({issue.reason}), "
                        f"retry {attempt + 1}/{max_retries}")
                    continue

                result = self._align_capture(result)
            except CaptureAlignmentError as exc:
                logger.warning(f"Preview: Alignment failed ({exc}), retry {attempt + 1}/{max_retries}")
                continue

            # Deconvolution + quality gate
            ir_full, ir_linear = self._process_capture(result)

            # 3. DSP Verification
            metrics = self.verifier.calculate_metrics(ir_full, ir_linear, result.get("psr", 0.0))

            issue = find_issue(
                self.detectors, "metrics", self._detector_context(result, metrics, ir_full, ir_linear))
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
                "raw_recordings": result['raw_recordings'],
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
