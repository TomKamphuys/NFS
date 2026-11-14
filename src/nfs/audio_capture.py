"""Sweep playback and raw recording, device discovery, tones and live meters.

No alignment, deconvolution, IR export or detector policy lives here.
"""

import ctypes
import sys
import threading
import time
from contextlib import ExitStack, contextmanager

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import scipy.signal
import scipy.fft
from loguru import logger

from .utils.dsp import DSPUtils
from . import _audio_backend as sd
from .audio_signals import _LINEAR_IR_ORIGIN_SAMPLE

BLOCKSIZE = 0

def _set_realtime_priority() -> None:
    """Set the Windows process to real-time priority to reduce audio buffer underruns.

    Elevates the entire Python process, including the PortAudio callback thread,
    so the audio callback is less likely to be preempted by Windows scheduler.
    This is a preventive measure that complements the retry-based dropout detection.
    Silently ignored on non-Windows systems or if the call fails.
    """
    if sys.platform != "win32":
        return
    try:
        REALTIME_PRIORITY_CLASS = 0x00000080
        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(),
            REALTIME_PRIORITY_CLASS
        )
        logger.debug("Process priority set to REALTIME_PRIORITY_CLASS")
    except Exception:
        logger.debug("Failed to set real-time process priority (non-critical)")


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
# Keep the nominal origin at this zero-based index in every linear export.
# The actual peak can occur later: speaker delay must not be aligned away.
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

    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = int(sample_rate)
        # Capacity absorbs brief scheduling stalls; it is not a delay target.
        # Processing is capped to the newest 100 ms so meters remain responsive.
        self._capacity = max(8192, self.sample_rate)
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
    catalog: Optional[dict] = None,
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
    if catalog is None:
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




class AudioCapture:
    """Own sweep preparation, raw recording, tone streams and metering."""

    def __init__(self, hw, cap, sweep_gen=None, marker_gen=None,
                 harmonic_injector=None, protection_filter=None, hardware_io=None):
        self.hw, self.cap = hw, cap
        self.sweep_gen, self.marker_gen = sweep_gen, marker_gen
        self.harmonic_injector, self.protection_filter = harmonic_injector, protection_filter
        self._hardware_io = hardware_io
        self._sine_stream = None
        self._sine_com_context = None
        self._sine_owner_thread = None
        self._sine_meter_worker = None
        self._sine_level_lock = threading.Lock()
        self._sine_level_dbfs = None

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


    def _capture_buffers(self, prepared: Dict[str, Any], recordings=None):
        """Local hardware implementation, run in the acquisition process in production.

        Direct construction without hardware_io remains available for driver tests.
        No generation, alignment, deconvolution or file access happens here.
        """
        if self.hw['ch_out_spkr'] == self.hw['ch_out_ref'] or self.hw['ch_in_mic'] == self.hw['ch_in_loop']:
            raise ValueError("Dual-channel capture requires distinct speaker/reference outputs and mic/loop inputs")
        tx_sweep_long = prepared['tx_speaker']
        tx_ref_long = prepared['tx_loop']
        total_len = len(tx_sweep_long)

        # Allocate one column per physical channel. Channel numbers in the
        # saved hardware settings are zero-based, even for ASIO devices.
        out_ch_count = max(self.hw['ch_out_spkr'], self.hw['ch_out_ref']) + 1

        out_frames = np.zeros((total_len, out_ch_count), dtype=np.float32)
        out_frames[:, self.hw['ch_out_spkr']] = tx_sweep_long
        out_frames[:, self.hw['ch_out_ref']] = tx_ref_long

        if recordings is None:
            rec_loop = np.zeros(total_len, dtype=np.float32)
            rec_mic = np.zeros(total_len, dtype=np.float32)
        else:
            rec_mic, rec_loop = recordings

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
        meter_worker = _AudioMeterWorker(self.hw['fs'])
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
            with _asio_thread_context(use_asio_in or use_asio_out), ExitStack() as resources:
                stream = sd.Stream(device=(self.hw['dev_in'], self.hw['dev_out']), samplerate=self.hw['fs'],
                           blocksize=self.hw['blocksize'],
                           dtype="float32", channels=(in_args[0], out_args[0]), dither_off=True,
                           extra_settings=(in_args[1], out_args[1]), callback=callback,
                           latency='high')
                try:
                    resources.enter_context(stream)
                except BaseException:
                    # Opening succeeded but __enter__/start failed. Do not
                    # leave an allocated device in the long-lived process.
                    stream.close()
                    raise
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

        return rec_mic, rec_loop


    def play_sine(self, frequency: float, level_dbfs: float, duration_s: Optional[float] = 1.0) -> None:
        """
        Plays a sine wave at the specified frequency and level.

        Both finite and continuous tones bypass the driver protection filter;
        that filter belongs to sweep playback only.

        :param frequency: Frequency in Hz.
        :param level_dbfs: Level in dBFS.
        :param duration_s: Duration in seconds. If None, plays until stop_sine() is called.
        """
        if self._hardware_io is not None:
            return self._hardware_io.play_sine(self.hw, frequency, level_dbfs, duration_s)
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
            meter_worker = _AudioMeterWorker(fs)
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
                with _asio_thread_context(use_asio_in or use_asio_out), ExitStack() as resources:
                    stream = sd.Stream(
                        device=(in_dev, out_dev),
                        samplerate=fs,
                        blocksize=self.hw['blocksize'],
                        dtype='float32',
                        channels=(in_args[0], out_ch_count),
                        dither_off=True,
                        extra_settings=(in_args[1], out_args_extra),
                        callback=callback,
                        latency='high',
                    )
                    try:
                        resources.enter_context(stream)
                    except BaseException:
                        stream.close()
                        raise
                    if not done_evt.wait(duration_s + 10):
                        raise RuntimeError("Audio tone did not finish within the expected duration")
            finally:
                meter_worker.stop()
                reset_audio_meter_state(active=False)
        else:
            # Indefinite playback
            phase = 0.0
            phase_inc = 2 * np.pi * frequency / fs
            self._sine_meter_worker = _AudioMeterWorker(fs)
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
                    blocksize=BLOCKSIZE,
                    channels=(in_args[0], out_ch_count),
                    extra_settings=(in_args[1], out_args_extra),
                    callback=callback,
                    dtype='float32',
                    dither_off=True,
                    latency='high',
                )
                self._sine_stream.start()
            except Exception:
                # Also close an allocated stream when start(), rather than
                # construction, fails; release COM only after closing it.
                self.stop_sine()
                raise


    def update_sine_level(self, level_dbfs: float) -> None:
        """Updates the level used by active continuous sine playback."""
        if self._hardware_io is not None:
            return self._hardware_io.update_sine_level(level_dbfs)
        with self._sine_level_lock:
            self._sine_level_dbfs = float(level_dbfs)


    def stop_sine(self) -> None:
        """Stop continuous playback on the same thread that started it."""

        if self._hardware_io is not None:
            return self._hardware_io.stop_sine()

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

