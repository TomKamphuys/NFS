import nfs.audio_capture as capture_io
"""Physical channel mapping and sweep-only protection regressions."""
import sys
from types import ModuleType
from unittest.mock import Mock

import numpy as np
import pytest

import nfs.audio as audio


@pytest.fixture
def sounddevice(monkeypatch):
    module = ModuleType("sounddevice")
    module.AsioSettings = lambda **kwargs: kwargs
    module.WasapiSettings = lambda **kwargs: kwargs
    module.Stream = None
    module.stop = lambda: None
    module._terminate = None
    module._initialize = None
    module.query_hostapis = None
    module.query_devices = None
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    monkeypatch.setattr(capture_io, "sd", module)
    return module


@pytest.mark.parametrize("api", ["Windows WASAPI", "MME"])
@pytest.mark.parametrize("operation", ["capture", "finite_sine"])
def test_non_asio_stream_preserves_physical_channel_columns(monkeypatch, sounddevice, api, operation):
    """Sparse/reordered physical channels must not become ASIO-style columns 0/1."""
    from test_audio_capture import make_engine
    engine = make_engine()
    engine.hw.update(ch_in_loop=3, ch_in_mic=1, ch_out_spkr=2, ch_out_ref=0,
                     wasapi_exclusive=True)
    monkeypatch.setattr(engine.capture_engine, '_get_api_name', lambda dev: api)
    monkeypatch.setattr(sounddevice, 'WasapiSettings', lambda **kw: kw)
    monkeypatch.setattr(sounddevice, 'stop', lambda: None)
    monkeypatch.setattr(capture_io, '_AudioMeterWorker', lambda *args: Mock())
    prepared = engine._prepare_sweep()
    monkeypatch.setattr(engine, '_prepare_sweep', lambda: prepared)
    monkeypatch.setattr(engine, '_finish_sweep', lambda p, mic, loop: (mic, loop))

    class Stream:
        samplerate = engine.hw['fs']
        latency = (0., 0.)

        def __init__(self, **kw):
            assert kw['channels'] == (4, 3)
            expected = {'exclusive': True} if api == 'Windows WASAPI' else None
            assert kw['extra_settings'] == (expected, expected)
            self.callback = kw['callback']

        def __enter__(self):
            n = prepared['timing']['total_samples'] if operation == 'capture' else round(.01*self.samplerate)
            inputs = np.tile(np.array([.1, .2, .3, .4], dtype=np.float32), (n, 1))
            outputs = np.full((n, 3), np.nan, dtype=np.float32)
            self.callback(inputs, outputs, n, None, False)
            assert np.all(outputs[:, 1] == 0)  # Unused physical output stays silent.
            if operation == 'capture':
                np.testing.assert_array_equal(outputs[:, 2], prepared['tx_speaker'])
                np.testing.assert_array_equal(outputs[:, 0], prepared['tx_loop'])
            else:
                np.testing.assert_array_equal(outputs[:, 2], outputs[:, 0])
                assert np.max(abs(outputs[:, 2])) > .1
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(sounddevice, 'Stream', Stream)
    if operation == 'capture':
        mic, loop = engine._run_sweep()
        assert np.all(mic == .2) and np.all(loop == .4)
    else:
        engine.play_sine(1000, -12, .01)


@pytest.mark.parametrize("continuous", [False, True])
def test_sine_bypasses_sweep_protection_in_both_modes(monkeypatch, sounddevice, continuous):
    from contextlib import nullcontext
    from test_audio_capture import make_engine
    engine = make_engine()
    fs = engine.hw['fs']
    engine.protection_filter = audio.ProtectionFilter(fs, 10000, 4)
    monkeypatch.setattr(engine.capture_engine, '_get_api_name', lambda dev: 'ASIO')
    monkeypatch.setattr(capture_io, '_asio_thread_context', lambda enabled: nullcontext())
    monkeypatch.setattr(sounddevice, 'AsioSettings', lambda **kw: kw)
    monkeypatch.setattr(sounddevice, 'stop', lambda: None)
    monkeypatch.setattr(capture_io, '_AudioMeterWorker', lambda *args: Mock())
    blocks = []
    count = round(.01 * fs)

    class Stream:
        def __init__(self, **kw):
            self.callback = kw['callback']

        def start(self):
            for size in (count // 2, count - count // 2):
                output = np.empty((size, 2), dtype=np.float32)
                self.callback(np.zeros_like(output), output, size, None, False)
                blocks.append(output.copy())

        def __enter__(self):
            self.start()
            return self

        def __exit__(self, *args):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sounddevice, 'Stream', Stream)
    engine.play_sine(500, -12, None if continuous else .01)
    if continuous:
        engine.stop_sine()
    output = np.concatenate(blocks)
    expected = np.sin(2*np.pi*500*np.arange(count)/fs) * 10**(-12/20)
    np.testing.assert_allclose(output[:, 0], expected, atol=3e-8, rtol=0)
    np.testing.assert_array_equal(output[:, 0], output[:, 1])
