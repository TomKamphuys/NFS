"""Real spawned-process integration tests using a synthetic device, never hardware."""

import os
import threading
import time

import numpy as np
import pytest

import nfs.audio_capture as audio
import nfs.audio_process as remote


class SyntheticDevice:
    """Spawn-picklable device whose replies expose process/thread ownership."""

    def __init__(self, hardware):
        self.hardware = hardware
        self.owner = threading.get_ident()
        self.events = []

    @staticmethod
    def get_devices_and_channels():
        return {7: dict(name='Synthetic ASIO', hostapi='ASIO', input_channels=[0, 1],
                        output_channels=[0, 1], pid=os.getpid(), thread=threading.get_ident())}

    @staticmethod
    def get_supported_sample_rates(*args):
        return [48000, 96000]

    def capture(self, buffers):
        assert threading.get_ident() == self.owner
        if self.hardware.get('failure') == 'exception':
            raise ValueError('synthetic capture failure')
        if self.hardware.get('failure') == 'crash':
            os._exit(19)
        buffers[2] = buffers[0] * 0.5
        buffers[3] = buffers[1] * 0.25
        if self.hardware.get('delay'):
            time.sleep(self.hardware['delay'])
        return dict(actual_sample_rate=48000.0, stream_latency_s=[0.01, 0.02],
                    stream_status=[dict(sample=128, status='output underflow')],
                    pid=os.getpid(), thread=self.owner, events=list(self.events))

    def play_sine(self, frequency, level, duration):
        assert threading.get_ident() == self.owner
        self.events.append(('sine', frequency, level, duration))
        samples = np.full((256, 2), 0.25, dtype=np.float32)
        audio.update_audio_meter_state(samples, samples, active=True)
        if self.hardware.get('delay'):
            time.sleep(self.hardware['delay'])
        if duration is not None:
            self.stop_sine()

    def update_sine_level(self, level):
        assert threading.get_ident() == self.owner
        self.events.append(('level', level))

    def stop_sine(self):
        assert threading.get_ident() == self.owner
        self.events.append(('stop',))
        audio.reset_audio_meter_state()


class CallbackStream:
    """PortAudio substitute running callbacks on another thread in the child."""

    samplerate = 48000
    latency = (.01, .02)
    frames_to_run = 0
    last_output = None

    def __init__(self, **settings):
        self.settings = settings
        self.owner = threading.get_ident()

    def start(self):
        errors = []
        output = []
        def pump():
            assert threading.get_ident() != self.owner
            remaining = self.frames_to_run
            for size in [64, 127, 513] * (remaining // 64 + 1):
                if remaining <= 0:
                    break
                frames = min(size, remaining)
                inputs = np.empty((frames, 2), dtype=np.float32)
                inputs[:, 0] = .125  # Selected ASIO loop input.
                inputs[:, 1] = .25   # Selected ASIO microphone input.
                outputs = np.full((frames, 2), np.nan, dtype=np.float32)
                try:
                    self.settings['callback'](inputs, outputs, frames, None, False)
                except Exception as exc:
                    errors.append(exc)
                    return
                output.append(outputs)
                remaining -= frames
        worker = threading.Thread(target=pump)
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
        if errors:
            raise errors[0]
        type(self).last_output = np.concatenate(output)

    def stop(self):
        assert threading.get_ident() == self.owner

    def close(self):
        assert threading.get_ident() == self.owner

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
        self.close()


class CallbackDevice(remote._HardwareSession):
    """Exercise production local capture/tone code inside the real child."""

    def __init__(self, hardware):
        from types import SimpleNamespace
        audio.sd = SimpleNamespace(
            Stream=CallbackStream,
            query_devices=lambda device: dict(hostapi=0),
            query_hostapis=lambda: [dict(name='ASIO')],
            AsioSettings=lambda **kw: kw,
        )
        super().__init__(hardware)

    def capture(self, buffers):
        CallbackStream.frames_to_run = buffers.shape[1]
        observed = super().capture(buffers)
        np.testing.assert_array_equal(CallbackStream.last_output[:, 0], buffers[0])
        np.testing.assert_array_equal(CallbackStream.last_output[:, 1], buffers[1])
        return observed

    def play_sine(self, frequency, level, duration):
        CallbackStream.frames_to_run = int(round(duration * 48000)) if duration is not None else 1024
        return super().play_sine(frequency, level, duration)


@pytest.fixture
def client():
    service = remote.AudioProcess(device_factory=SyntheticDevice)
    yield service
    service.close()
    assert service.pid is None


def hardware(**kwargs):
    return dict(fs=48000, **kwargs)


def test_lazy_start_and_unused_stop_do_not_spawn(client):
    client.stop_sine()
    client.update_sine_level(-20)
    assert client.get_audio_meter_state()['active'] is False
    assert client.pid is None


def test_discovery_and_capture_share_one_child_owner_with_exact_pcm(client):
    device = client.get_devices_and_channels()[7]
    assert device['pid'] != os.getpid()
    assert device['pid'] == client.pid
    assert client.get_supported_sample_rates(7, 7) == [48000, 96000]
    speaker = np.linspace(-0.8, 0.8, 4097, dtype=np.float32)
    reference = np.cos(np.arange(4097)).astype(np.float32)
    for _ in range(3):
        mic, loop, observed = client.capture(hardware(), speaker, reference)
        np.testing.assert_array_equal(mic, speaker * 0.5)
        np.testing.assert_array_equal(loop, reference * 0.25)
        assert observed['pid'] == device['pid']
        assert observed['thread'] == device['thread']
        assert observed['stream_status'][0]['sample'] == 128
    # Returned data outlives shared memory and subsequent captures.
    assert mic.flags.owndata and loop.flags.owndata


def test_continuous_tone_level_meter_and_capture_handoff(client):
    client.play_sine(hardware(), 440, -20, None)
    snapshot = client.get_audio_meter_state()
    assert snapshot['active'] is True
    assert snapshot['outputs'][0]['peak_dbfs'] == pytest.approx(-12.0412, abs=.001)
    client.update_sine_level(-30)
    samples = np.ones(1024, dtype=np.float32)
    _, _, observed = client.capture(hardware(), samples, samples)
    assert ('sine', 440, -20, None) in observed['events']
    assert ('level', -30) in observed['events']
    assert observed['events'][-1] == ('stop',)
    assert client.get_audio_meter_state()['active'] is False


def test_finite_tone_stops_before_reply(client):
    client.play_sine(hardware(), 1000, -18, 0.01)
    assert client.get_audio_meter_state()['active'] is False


def test_device_error_propagates_and_next_capture_can_succeed(client):
    samples = np.ones(1024, dtype=np.float32)
    with pytest.raises(ValueError, match='synthetic capture failure'):
        client.capture(hardware(failure='exception'), samples, samples)
    assert client.capture(hardware(), samples, samples)[2]['pid'] == client.pid


@pytest.mark.parametrize('failure', ['exception', 'crash'])
def test_failed_capture_releases_shared_memory(client, monkeypatch, failure):
    segments = []
    original = remote.SharedMemory
    def allocate(*args, **kwargs):
        segment = original(*args, **kwargs)
        if kwargs.get('create'):
            segments.append(segment.name)
        return segment
    monkeypatch.setattr(remote, 'SharedMemory', allocate)
    samples = np.ones(1024, dtype=np.float32)
    with pytest.raises((ValueError, RuntimeError)):
        client.capture(hardware(failure=failure), samples, samples)
    assert len(segments) == 1
    with pytest.raises(FileNotFoundError):
        original(name=segments[0])
    if failure == 'crash':
        assert client.pid is None
        with pytest.raises(RuntimeError, match='reload'):
            client.get_devices_and_channels()


def test_response_timeout_terminates_child(client):
    client.get_devices_and_channels()  # Startup is deliberately outside the short deadline.
    with pytest.raises(TimeoutError):
        client._call('sine', hardware(delay=5), 440, -20, None, timeout=0.1)
    assert client.pid is None


def test_meters_do_not_wait_for_ongoing_capture(client):
    client.get_devices_and_channels()
    samples = np.ones(1024, dtype=np.float32)
    errors = []
    def capture():
        try:
            client.capture(hardware(delay=.5), samples, samples)
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=capture)
    worker.start()
    try:
        deadline = time.monotonic() + .2
        reads = 0
        while time.monotonic() < deadline:
            client.get_audio_meter_state()
            reads += 1
        assert worker.is_alive()
        assert reads > 1
    finally:
        worker.join(5)
    assert not errors


def test_audio_keeps_preparation_and_finishing_in_parent(client, monkeypatch):
    from test_audio_asio import make_engine
    engine = make_engine()
    engine._hardware_io = client
    engine.hw = hardware()
    speaker = np.arange(1024, dtype=np.float32) / 1024
    reference = -speaker
    prepared = dict(tx_speaker=speaker, tx_loop=reference, timing={})
    def prepare():
        assert os.getpid() != client.pid
        return prepared
    def finish(plan, mic, loop):
        assert plan is prepared
        assert os.getpid() != plan['timing']['pid']
        np.testing.assert_array_equal(mic, speaker * 0.5)
        np.testing.assert_array_equal(loop, reference * 0.25)
        return 'processed in parent'
    monkeypatch.setattr(engine, '_prepare_sweep', prepare)
    monkeypatch.setattr(engine, '_finish_sweep', finish)
    assert engine._run_sweep() == 'processed in parent'


def test_graceful_shutdown_joins_child(client):
    client.play_sine(hardware(), 440, -20, None)
    process = client._process
    client.close()
    assert client.pid is None
    assert process._closed


def test_production_capture_and_tones_execute_inside_child():
    service = remote.AudioProcess(device_factory=CallbackDevice)
    settings = dict(dev_in=7, dev_out=7, ch_in_loop=1, ch_in_mic=0,
                    ch_out_spkr=0, ch_out_ref=1, fs=48000, blocksize=0,
                    wasapi_exclusive=False)
    samples = np.linspace(-.5, .5, 4097, dtype=np.float32)
    try:
        mic, loop, observed = service.capture(settings, samples, -samples)
        assert np.all(mic == .25)
        assert np.all(loop == .125)
        assert observed['stream_status'] == []
        assert observed['actual_sample_rate'] == 48000
        service.play_sine(settings, 440, -20, .01)
        service.play_sine(settings, 440, -20, None)
        service.update_sine_level(-30)
        service.stop_sine()
    finally:
        service.close()


def test_failed_global_client_is_replaced_for_reload(monkeypatch):
    failed = remote.AudioProcess(device_factory=SyntheticDevice)
    failed._broken = True
    monkeypatch.setattr(remote, '_CLIENT', failed)
    replacement = remote.get_audio_process()
    try:
        assert replacement is not failed
        assert replacement.pid is None
    finally:
        remote.shutdown_audio_process()


def test_parent_does_not_import_sounddevice(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    script = tmp_path / 'probe.py'
    source = str(Path(__file__).resolve().parents[1] / 'src')
    tests = str(Path(__file__).resolve().parent)
    script.write_text(
        f'import sys\nsys.path[:0] = [{source!r}, {tests!r}]\n'
        'from nfs.audio_process import AudioProcess\n'
        'from test_audio_process import SyntheticDevice\n'
        'if __name__ == "__main__":\n'
        '    client = AudioProcess(device_factory=SyntheticDevice)\n'
        '    try:\n'
        '        client.get_devices_and_channels()\n'
        '        assert "sounddevice" not in sys.modules\n'
        '    finally:\n'
        '        client.close()\n', encoding='utf-8')
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
