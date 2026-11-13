"""Isolated hardware acquisition with one stream-owning process/thread.

The parent generates sweeps, controls motion, processes recordings and saves
files. Only commands and small replies use the pipe; complete playback/capture
buffers use shared memory, with no IPC on the audio callback path.
"""

from __future__ import annotations

import atexit
import multiprocessing as mp
from multiprocessing.shared_memory import SharedMemory
import threading
import time
import sys

import numpy as np


_METER_GROUPS = ("outputs", "inputs", "a_weighted_inputs", "c_weighted_inputs")
_METER_FIELDS = ("rms_dbfs", "peak_dbfs", "clip")
_METER_SIZE = 2 + len(_METER_GROUPS) * 2 * len(_METER_FIELDS)


class _HardwareSession:
    """Reuse the local stream implementation without constructing any DSP pipeline."""

    def __init__(self, hardware):
        from .audio import Audio

        self.engine = Audio(
            hardware, dict(num_sweeps=1, sweep_dur_s=0, debug_saves=False),
            None, None, None, None,
        )

    @staticmethod
    def get_devices_and_channels():
        from .audio import get_devices_and_channels
        return get_devices_and_channels()

    @staticmethod
    def get_supported_sample_rates(*args):
        from .audio import get_supported_sample_rates
        return get_supported_sample_rates(*args)

    def capture(self, buffers):
        prepared = dict(tx_speaker=buffers[0], tx_loop=buffers[1], timing={})
        self.engine._capture_buffers(prepared, recordings=(buffers[2], buffers[3]))
        return prepared['timing']

    def play_sine(self, frequency, level, duration):
        self.engine.play_sine(frequency, level, duration)

    def update_sine_level(self, level):
        self.engine.update_sine_level(level)

    def stop_sine(self):
        self.engine.stop_sine()


def _publish_meter(shared, state):
    values = [float(state['active']), state['updated_at']]
    for group in _METER_GROUPS:
        for channel in state[group]:
            values.extend(float(channel[field]) for field in _METER_FIELDS)
    # Called by the meter worker (or stream owner), never the audio callback.
    with shared.get_lock():
        shared[:] = values


def _serve(connection, meter, device_factory):
    """Spawn entry point. All backend access and stream lifetime stay here."""
    from . import audio
    from loguru import logger

    # The child must not write unassociated warnings to the parent's console.
    # Forward logs over a separate pipe message, outside the audio callback.
    send_lock = threading.Lock()

    def send(message):
        with send_lock:
            connection.send(message)

    def forward_log(message):
        record = message.record
        send(('log', record['level'].name, record['message']))

    logger.remove()
    logger.add(forward_log, level='WARNING', catch=True)
    original_update = audio.update_audio_meter_state

    def update_meter(*args, **kwargs):
        original_update(*args, **kwargs)
        _publish_meter(meter, audio.get_audio_meter_state())

    audio.update_audio_meter_state = update_meter
    device = None
    hardware = None
    graceful_shutdown = False
    try:
        # COM precedes even the first PortAudio import/discovery, and outlives
        # every stream. This context is on the process's command-owning thread.
        with audio._asio_thread_context(True):
            try:
                send(('ready',))
                while True:
                    try:
                        operation, args = connection.recv()
                    except EOFError:
                        break
                    try:
                        if operation == 'shutdown':
                            graceful_shutdown = True
                            break
                        if operation == 'devices':
                            result = device_factory.get_devices_and_channels()
                        elif operation == 'rates':
                            result = device_factory.get_supported_sample_rates(*args)
                        elif operation in ('capture', 'sine'):
                            settings, *payload = args
                            if device is None or settings != hardware:
                                if device is not None:
                                    device.stop_sine()
                                device = None
                                device = device_factory(settings)
                                hardware = dict(settings)
                            device.stop_sine()
                            if operation == 'capture':
                                name, frames = payload
                                segment = SharedMemory(name=name)
                                buffers = np.ndarray((4, frames), dtype=np.float32, buffer=segment.buf)
                                try:
                                    result = device.capture(buffers)
                                finally:
                                    del buffers
                                    segment.close()
                            else:
                                result = device.play_sine(*payload)
                        elif operation == 'level':
                            result = device.update_sine_level(*args) if device is not None else None
                        elif operation == 'stop':
                            result = device.stop_sine() if device is not None else None
                        else:
                            raise ValueError(f'Unknown audio command: {operation}')
                        send(('result', result))
                    except Exception as exc:
                        # Send text rather than pickling arbitrary driver exceptions.
                        send(('error', type(exc).__name__, str(exc)))
            finally:
                try:
                    if device is not None:
                        device.stop_sine()
                finally:
                    audio.reset_audio_meter_state()
                    # Terminate the library on its initializing thread before
                    # releasing COM, rather than deferring this to interpreter
                    # teardown. This runs once, only in the isolated child.
                    backend = sys.modules.get('sounddevice')
                    if backend is not None:
                        while getattr(backend, '_initialized', 0):
                            backend._terminate()
        if graceful_shutdown:
            send(('result', None))
    except Exception as exc:
        try:
            send(('error', type(exc).__name__, str(exc)))
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


class AudioProcess:
    """Lazy, serialized acquisition client with bounded waits and crash cleanup.

    device_factory is an injectable, spawn-picklable local device implementation
    for process integration tests. Production uses the ASIO/PortAudio session.
    """

    def __init__(self, *, device_factory=_HardwareSession):
        self._context = mp.get_context('spawn')
        self._device_factory = device_factory
        self._lock = threading.RLock()
        self._process = None
        self._connection = None
        self._meter = self._context.Array('d', _METER_SIZE)
        self._last_meter = None
        self._broken = False

    @property
    def pid(self):
        return self._process.pid if self._process is not None else None

    def _start(self):
        if self._broken:
            raise RuntimeError('Audio process failed; reload the audio backend before retrying')
        if self._process is not None:
            return
        parent, child = self._context.Pipe()
        process = self._context.Process(
            target=_serve, args=(child, self._meter, self._device_factory),
            name='nfs-audio-acquisition', daemon=True,
        )
        self._process, self._connection = process, parent
        try:
            process.start()
            child.close()
            reply = self._receive(30.0)
            if reply != ('ready',):
                detail = ': ' + reply[2] if reply[0] == 'error' else ''
                raise RuntimeError('Audio process did not initialize' + detail)
        except BaseException:
            child.close()
            self._broken = True
            self._dispose()
            raise

    def _receive(self, timeout):
        from loguru import logger

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._connection.poll(0.05):
                try:
                    reply = self._connection.recv()
                except (EOFError, OSError) as exc:
                    raise RuntimeError('Audio acquisition process disconnected') from exc
                if reply[0] == 'log':
                    logger.log(reply[1], 'Audio process: {}', reply[2])
                    continue
                return reply
            if not self._process.is_alive():
                raise RuntimeError(f'Audio acquisition process exited (code {self._process.exitcode})')
        raise TimeoutError('Audio acquisition process did not respond in time')

    def _call(self, operation, *args, timeout=30.0):
        with self._lock:
            self._start()
            try:
                self._connection.send((operation, args))
                reply = self._receive(timeout)
            except (OSError, RuntimeError, TimeoutError):
                self._broken = True
                self._dispose()
                raise
            if reply[0] == 'error':
                if reply[1] == 'ValueError':
                    raise ValueError(reply[2])
                raise RuntimeError(f'Audio process {reply[1]}: {reply[2]}')
            if reply[0] != 'result':
                self._broken = True
                self._dispose()
                raise RuntimeError('Unexpected audio process response')
            return reply[1]

    def get_devices_and_channels(self):
        return self._call('devices')

    def get_supported_sample_rates(self, input_device, output_device):
        return self._call('rates', input_device, output_device)

    def capture(self, hardware, speaker, reference):
        if speaker.ndim != 1 or reference.ndim != 1 or len(speaker) != len(reference) or not len(speaker):
            raise ValueError('Playback requires two nonempty, equally sized mono buffers')
        frames = len(speaker)
        segment = SharedMemory(create=True, size=4 * frames * np.dtype(np.float32).itemsize)
        buffers = np.ndarray((4, frames), dtype=np.float32, buffer=segment.buf)
        try:
            # Touch all pages before starting the device, including both inputs.
            buffers.fill(0)
            buffers[0] = speaker
            buffers[1] = reference
            observed = self._call('capture', dict(hardware), segment.name, frames,
                                  timeout=frames / hardware['fs'] + 15.0)
            return buffers[2].copy(), buffers[3].copy(), observed
        finally:
            del buffers
            segment.close()
            segment.unlink()

    def play_sine(self, hardware, frequency, level, duration):
        return self._call('sine', dict(hardware), frequency, level, duration,
                          timeout=30.0 + (duration or 0.0))

    def update_sine_level(self, level):
        # Updating/stopping unused audio must not start a driver process.
        with self._lock:
            if self._process is not None:
                return self._call('level', level)

    def stop_sine(self):
        with self._lock:
            if self._process is not None:
                return self._call('stop')

    def get_audio_meter_state(self):
        from .audio import get_audio_meter_state

        # Meter reads do not wait for the command pipe or an ongoing capture.
        # Return the last snapshot rather than blocking the UI on a dead child.
        local_state = get_audio_meter_state()
        process = self._process
        try:
            alive = process is not None and process.is_alive()
        except ValueError:  # Concurrent shutdown has closed the process handle.
            alive = False
        if not alive:
            return local_state
        if self._meter.get_lock().acquire(False):
            try:
                values = list(self._meter[:])
            finally:
                self._meter.get_lock().release()
            if values[1] and time.time() - values[1] <= 1.5:
                state = dict(active=bool(values[0]), updated_at=values[1])
                index = 2
                for group in _METER_GROUPS:
                    state[group] = []
                    for _ in range(2):
                        state[group].append(dict(rms_dbfs=values[index], peak_dbfs=values[index + 1],
                                                 clip=bool(values[index + 2])))
                        index += 3
                self._last_meter = state
            else:
                self._last_meter = local_state
        state = self._last_meter or local_state
        # Mock acquisition remains in the parent, including after switching
        # from a hardware configuration whose process is still idle.
        if local_state['active'] and local_state['updated_at'] > state['updated_at']:
            return local_state
        return state

    def _dispose(self):
        if self._process is not None:
            if self._process.pid is not None:
                self._process.join(timeout=0.2)
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(timeout=2.0)
                if self._process.is_alive():
                    self._process.kill()
                    self._process.join(timeout=2.0)
            self._process.close()
            self._process = None
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._last_meter = None

    def close(self):
        with self._lock:
            try:
                if self._process is not None and self._process.is_alive():
                    self._call('shutdown', timeout=5.0)
            except (OSError, RuntimeError, TimeoutError):
                pass
            finally:
                self._dispose()


_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def get_audio_process():
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT._broken:
            _CLIENT = AudioProcess()
        return _CLIENT


def shutdown_audio_process():
    global _CLIENT
    with _CLIENT_LOCK:
        client, _CLIENT = _CLIENT, None
    if client is not None:
        client.close()


def get_audio_meter_state():
    """UI entry point for process meters, with an in-process mock fallback."""
    return get_audio_process().get_audio_meter_state()


atexit.register(shutdown_audio_process)
