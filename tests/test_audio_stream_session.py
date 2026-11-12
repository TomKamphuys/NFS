"""Measurement-set stream lifetime regressions using a threaded duplex device."""

from contextlib import contextmanager
from types import SimpleNamespace
import threading
from unittest.mock import Mock

import numpy as np
import pytest

import nfs.audio as audio
from nfs.datatypes import CylindricalPosition
from nfs.nfs import NearFieldScanner
from test_audio_asio import make_engine


@pytest.fixture
def device(monkeypatch):
    streams = []
    lifecycle = []

    @contextmanager
    def com_context(enabled):
        owner = threading.get_ident()
        lifecycle.append("com_enter")
        try:
            yield
        finally:
            assert threading.get_ident() == owner
            lifecycle.append("com_exit")

    class Stream:
        samplerate = 48000
        latency = (0.01, 0.02)

        def __init__(self, **settings):
            self.settings = settings
            self.closed = threading.Event()
            self.idle = threading.Event()
            self.input_value = 0.25
            self.failure = None
            self.outputs = []
            self.thread = threading.Thread(target=self.pump, daemon=True)
            streams.append(self)

        def __enter__(self):
            assert audio.get_audio_meter_state()['stream_open'] is True
            lifecycle.append("start")
            self.thread.start()
            return self

        def pump(self):
            while not self.closed.is_set():
                inputs = np.full((1024, self.settings['channels'][0]), self.input_value, dtype=np.float32)
                outputs = np.full((1024, self.settings['channels'][1]), np.nan, dtype=np.float32)
                try:
                    self.settings['callback'](inputs, outputs, 1024, None, False)
                except Exception as exc:
                    self.failure = exc
                    return
                if np.any(outputs):
                    self.outputs.append(outputs.copy())
                else:
                    self.idle.set()
                self.closed.wait(0.001)

        def __exit__(self, *args):
            self.close()

        def close(self):
            assert audio.get_audio_meter_state()['stream_open'] is True
            lifecycle.append("close")
            self.closed.set()
            if self.thread.is_alive():
                self.thread.join(timeout=2)
            assert not self.thread.is_alive()

    monkeypatch.setattr(audio, 'sd', SimpleNamespace(
        Stream=Stream, AsioSettings=lambda **kw: kw, WasapiSettings=lambda **kw: kw))
    monkeypatch.setattr(audio, '_asio_thread_context', com_context)
    monkeypatch.setattr(audio, '_AudioMeterWorker', lambda *args: Mock())
    return SimpleNamespace(streams=streams, lifecycle=lifecycle)


def configure_engine(monkeypatch, api='ASIO', enabled=True):
    engine = make_engine()
    engine.hw['keep_stream_open'] = enabled
    monkeypatch.setattr(engine, '_get_api_name', lambda dev: api)
    monkeypatch.setattr(engine, '_finish_sweep', lambda prepared, mic, loop: (prepared, mic, loop))
    return engine


@pytest.mark.parametrize('api', ['ASIO', 'Windows WASAPI', 'MME'])
def test_session_reuses_stream_with_independent_captures_and_one_settle(monkeypatch, device, api):
    engine = configure_engine(monkeypatch, api)
    with engine.measurement_session():
        stream = device.streams[0]
        assert stream.idle.wait(2)
        first, first_mic, first_loop = engine._run_sweep()
        stream.input_value = 0.5
        stream.idle.clear()
        assert stream.idle.wait(2)
        second, second_mic, second_loop = engine._run_sweep()
        assert len(device.streams) == 1
        assert not stream.closed.is_set()
        assert audio.get_audio_meter_state()['stream_open'] is True
        audio.reset_audio_meter_state()
        assert audio.get_audio_meter_state()['stream_open'] is True
        assert first['timing']['pre_samples'] == round(engine.cap['pre_sil_ms'] * 48)
        assert second['timing']['pre_samples'] == 0
        assert first['timing']['total_samples'] - second['timing']['total_samples'] == first['timing']['pre_samples']
        assert first['timing']['stream_latency_s'] == [0.01, 0.02]
        assert second['timing']['actual_sample_rate'] == 48000
        assert np.all(first_mic == 0.25) and np.all(first_loop == 0.25)
        assert np.all(second_mic == 0.5) and np.all(second_loop == 0.5)
        assert not np.shares_memory(first_mic, second_mic)
        assert not np.shares_memory(first_loop, second_loop)
    assert device.lifecycle == ['com_enter', 'start', 'close', 'com_exit']
    assert engine._sweep_stream is None
    assert audio.get_audio_meter_state()['stream_open'] is False
    assert stream.failure is None


@pytest.mark.parametrize('enabled', [False, True])
def test_standalone_captures_always_open_close_and_settle(monkeypatch, device, enabled):
    engine = configure_engine(monkeypatch, enabled=enabled)
    for _ in range(2):
        prepared, _, _ = engine._run_sweep()
        assert prepared['timing']['pre_samples'] > 0
        assert device.streams[-1].closed.is_set()
        assert audio.get_audio_meter_state()['stream_open'] is False
    assert len(device.streams) == 2


def test_disabled_option_opens_stream_per_point(monkeypatch, device):
    engine = configure_engine(monkeypatch, enabled=False)
    with engine.measurement_session():
        for _ in range(2):
            prepared, _, _ = engine._run_sweep()
            assert prepared['timing']['pre_samples'] > 0
            assert device.streams[-1].closed.is_set()
    assert len(device.streams) == 2


@pytest.mark.parametrize('outcome', ['complete', 'stop', 'motion_failure', 'capture_failure'])
def test_scanner_owns_session_cleanup(monkeypatch, device, tmp_path, outcome):
    engine = configure_engine(monkeypatch)
    scanner = Mock()
    scanner.get_position.return_value = CylindricalPosition(100, 0, 0)
    motion = Mock()
    motion.total_points.return_value = 2
    motion.ready.side_effect = [False, False, True]
    if outcome == 'motion_failure':
        motion.next.side_effect = RuntimeError('motion failed')
    app = NearFieldScanner(scanner, engine, motion, str(tmp_path / 'positions.csv'))
    app.set_project_directory(tmp_path)
    captures = []

    def measure(*args):
        captures.append(engine._run_sweep())
        if outcome == 'capture_failure':
            raise RuntimeError('capture failed')
        if outcome == 'stop':
            app.stop_measurement_set()

    monkeypatch.setattr(engine, 'measure_ir', measure)
    if outcome.endswith('failure'):
        with pytest.raises(RuntimeError, match='failed'):
            app.take_measurement_set()
    else:
        app.take_measurement_set()
    assert len(device.streams) == 1
    assert device.streams[0].closed.is_set()
    assert engine._sweep_stream is None
    assert audio.get_audio_meter_state()['stream_open'] is False
    assert not app.is_measurement_set_running()
    assert len(captures) == {'complete': 2, 'stop': 1, 'motion_failure': 0, 'capture_failure': 1}[outcome]
    assert device.lifecycle[-2:] == ['close', 'com_exit']


def test_new_session_restores_driver_settle(monkeypatch, device):
    engine = configure_engine(monkeypatch)
    for _ in range(2):
        with engine.measurement_session():
            prepared, _, _ = engine._run_sweep()
            assert prepared['timing']['pre_samples'] > 0
    assert len(device.streams) == 2


def test_stream_start_failure_closes_device_before_com_exit(monkeypatch, device):
    engine = configure_engine(monkeypatch)
    stream_type = audio.sd.Stream

    class BrokenStream(stream_type):
        def __enter__(self):
            raise RuntimeError('start failed')

    monkeypatch.setattr(audio.sd, 'Stream', BrokenStream)
    with pytest.raises(RuntimeError, match='start failed'):
        with engine.measurement_session():
            pytest.fail('Session must not start')
    assert device.streams[0].closed.is_set()
    assert device.lifecycle == ['com_enter', 'close', 'com_exit']
    assert engine._sweep_stream is None
    assert audio.get_audio_meter_state()['stream_open'] is False


def test_callback_failure_closes_session(monkeypatch, device):
    engine = configure_engine(monkeypatch)
    with pytest.raises(RuntimeError, match='callback failed'):
        with engine.measurement_session():
            def fail(*args):
                raise ValueError('meter failed')
            engine._sweep_stream._meter_worker.submit.side_effect = fail
            engine._run_sweep()
    assert device.streams[0].closed.is_set()
    assert engine._sweep_stream is None
    assert audio.get_audio_meter_state()['stream_open'] is False


def test_capture_timeout_closes_session(monkeypatch, device):
    engine = configure_engine(monkeypatch)
    capture_type = audio._SweepCapture

    class TimedOutCapture(capture_type):
        def __init__(self, *args):
            super().__init__(*args)
            self.done = Mock()
            self.done.wait.return_value = False
            self.done.is_set.return_value = False

    monkeypatch.setattr(audio, '_SweepCapture', TimedOutCapture)
    with pytest.raises(RuntimeError, match='expected duration'):
        with engine.measurement_session():
            engine._run_sweep()
    assert device.streams[0].closed.is_set()
    assert engine._sweep_stream is None
    assert audio.get_audio_meter_state()['stream_open'] is False


def test_capture_rejects_a_different_calling_thread(monkeypatch, device):
    engine = configure_engine(monkeypatch)
    errors = []

    def capture():
        try:
            engine._run_sweep()
        except RuntimeError as exc:
            errors.append(str(exc))

    with engine.measurement_session():
        caller = threading.Thread(target=capture)
        caller.start()
        caller.join(timeout=2)
        assert not caller.is_alive()
        assert errors == ['Sweep captures must run on the thread that opened the stream']
        engine._run_sweep()
    assert device.streams[0].closed.is_set()


def test_pause_resume_retains_stream_and_settles_once(monkeypatch, device, tmp_path):
    engine = configure_engine(monkeypatch)
    scanner = Mock()
    scanner.get_position.return_value = CylindricalPosition(100, 0, 0)
    motion = Mock()
    motion.total_points.return_value = 2
    motion.ready.side_effect = [False, False, True]
    app = NearFieldScanner(scanner, engine, motion, str(tmp_path / 'positions.csv'))
    app.set_project_directory(tmp_path)
    captures = []
    resumed = threading.Event()
    timers = []

    def resume():
        assert len(device.streams) == 1
        assert not device.streams[0].closed.is_set()
        app.resume_measurement_set()
        resumed.set()

    def measure(*args):
        captures.append(engine._run_sweep())
        if len(captures) == 1:
            app.pause_measurement_set()
            timer = threading.Timer(0.15, resume)
            timers.append(timer)
            timer.start()

    monkeypatch.setattr(engine, 'measure_ir', measure)
    try:
        app.take_measurement_set()
    finally:
        for timer in timers:
            timer.join(timeout=2)
    assert resumed.is_set()
    assert len(device.streams) == 1
    assert captures[0][0]['timing']['pre_samples'] > 0
    assert captures[1][0]['timing']['pre_samples'] == 0
    assert device.streams[0].closed.is_set()
