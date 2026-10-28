"""ASIO routing and Windows COM lifetime tests; no audio device is opened."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import nfs.audio as audio
from nfs.naming import NamingConventionFactory
from nfs.debug_artifacts import DebugArtifactFactory


def make_engine():
    fs = 48000
    hw = dict(dev_in=18, dev_out=18, ch_in_loop=1, ch_in_mic=0,
              ch_out_spkr=0, ch_out_ref=1, fs=fs, blocksize=256, wasapi_exclusive=False)
    cap = dict(num_sweeps=1, sweep_dur_s=.5, sweep_level_dbfs=-12,
               pre_sil_ms=50, post_sil_ms=50, debug_saves=False)
    return audio.Audio(
        hw,
        cap,
        audio.SweepGenerator(fs, .5, 1, -12),
        audio.MarkerGenerator(fs, 100, (500, 5000), -12),
        audio.AlignmentEngine(fs, 1, False, 20, 100),
        audio.DeconvolutionEngine(fs),
        NamingConventionFactory.create('dimitri'),
        DebugArtifactFactory.create(False, hw),
    )


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
    return module


@pytest.fixture
def ole32(monkeypatch):
    ole = SimpleNamespace(CoInitializeEx=Mock(return_value=0), CoUninitialize=Mock())
    monkeypatch.setattr(audio.sys, "platform", "win32")
    monkeypatch.setattr(audio.ctypes, "windll", SimpleNamespace(ole32=ole), raising=False)
    return ole


def test_stopping_unused_audio_does_not_load_backend(monkeypatch):
    def forbidden(name):
        pytest.fail("Stopping unused audio must not import sounddevice")

    monkeypatch.setattr(audio.sd, "import_module", forbidden)
    engine = make_engine()
    engine.stop_sine()
    engine.stop_sine()


@pytest.mark.parametrize("result,releases", [(0, 1), (1, 1), (-2147417850, 0)])
def test_com_reference_ownership(ole32, result, releases):
    ole32.CoInitializeEx.return_value = result
    with audio._asio_thread_context(True):
        ole32.CoInitializeEx.assert_called_once_with(None, 2)
        ole32.CoUninitialize.assert_not_called()
    assert ole32.CoUninitialize.call_count == releases


def test_com_cleanup_on_stream_error(ole32):
    with pytest.raises(RuntimeError, match="stream failed"):
        with audio._asio_thread_context(True):
            raise RuntimeError("stream failed")
    ole32.CoUninitialize.assert_called_once_with()


def test_com_failure_does_not_enter_stream_or_uninitialize(ole32):
    ole32.CoInitializeEx.return_value = -2147467259  # E_FAIL
    with pytest.raises(RuntimeError, match="0x80004005"):
        with audio._asio_thread_context(True):
            pytest.fail("COM initialization failure must prevent stream creation")
    ole32.CoUninitialize.assert_not_called()


@pytest.mark.parametrize("platform,enabled", [("linux", True), ("win32", False)])
def test_non_asio_or_non_windows_does_not_touch_com(monkeypatch, ole32, platform, enabled):
    monkeypatch.setattr(audio.sys, "platform", platform)
    with audio._asio_thread_context(enabled):
        pass
    ole32.CoInitializeEx.assert_not_called()
    ole32.CoUninitialize.assert_not_called()


@pytest.mark.parametrize("operation", ["capture", "finite_sine"])
def test_stream_closes_before_com_release_and_uses_asio_roles(
        monkeypatch, ole32, sounddevice, operation):
    fs = 48000
    hw = dict(
        dev_in=18,
        dev_out=18,
        ch_in_loop=2,
        ch_in_mic=3,
        ch_out_spkr=3,
        ch_out_ref=1,
        fs=fs,
        blocksize=256,
        wasapi_exclusive=False,
    )
    cap = dict(
        num_sweeps=1,
        sweep_dur_s=0.5,
        sweep_level_dbfs=-12,
        pre_sil_ms=50,
        post_sil_ms=50,
        debug_saves=False,
    )
    engine = audio.Audio(
        hw,
        cap,
        audio.SweepGenerator(fs, 0.5, 1, -12),
        audio.MarkerGenerator(fs, 100, (500, 5000), -12),
        audio.AlignmentEngine(fs, 1, False, 20, 100),
        audio.DeconvolutionEngine(fs),
        NamingConventionFactory.create('dimitri'),
        DebugArtifactFactory.create(False, hw),
    )
    monkeypatch.setattr(engine, "_get_api_name", lambda dev: "ASIO")
    meter = Mock()
    monkeypatch.setattr(audio, "_AudioMeterWorker", lambda *args: meter)
    # Exercise the original master sequence; substitute only DSP alignment.
    # Its two inputs let us verify the ASIO physical-channel selectors.
    capture_samples = int(fs * (0.05 + 0.5 + 0.05))
    monkeypatch.setattr(engine.alignment_engine, "sync_and_average",
                        lambda mic, loop, *args: (mic, loop, [], 3.0))
    events = []
    ole32.CoUninitialize.side_effect = lambda: events.append("com_release")

    class Stream:
        samplerate = fs
        latency = (0.01, 0.02)

        def __init__(self, **kwargs):
            ole32.CoInitializeEx.assert_called_once_with(None, 2)
            ole32.CoUninitialize.assert_not_called()
            assert kwargs["device"] == (18, 18)
            assert kwargs["channels"] == (2, 2)
            assert kwargs["extra_settings"] == (
                {"channel_selectors": [2, 3]},
                {"channel_selectors": [3, 1]},
            )
            self.callback = kwargs["callback"]

        def __enter__(self):
            n = capture_samples if operation == "capture" else 480
            inputs = np.column_stack((np.full(n, 0.125), np.full(n, 0.25))).astype(np.float32)
            outputs = np.zeros((n, 2), dtype=np.float32)
            self.callback(inputs, outputs, n, None, False)
            if operation == "capture":
                assert np.max(np.abs(outputs[:, 0])) > 0.1
                assert np.max(np.abs(outputs[:, 1])) > 0.1
                assert not np.array_equal(outputs[:, 0], outputs[:, 1])
            else:
                assert np.max(np.abs(outputs)) > 0.1
                np.testing.assert_array_equal(outputs[:, 0], outputs[:, 1])
            return self

        def __exit__(self, *args):
            ole32.CoUninitialize.assert_not_called()
            events.append("stream_closed")

    monkeypatch.setattr(sounddevice, "Stream", Stream)
    if operation == "capture":
        result = engine._run_sweep()
        assert np.all(result['rx_mic_conditioned'] == 0.25)
        assert np.all(result['rx_loop_aligned'] == 0.125)
    else:
        engine.play_sine(1000, -12, 0.01)
    assert events == ["stream_closed", "com_release"]
    meter.stop.assert_called_once_with()


@pytest.mark.parametrize('failure', [None, 'open', 'start', 'stop'])
def test_continuous_sine_owns_com_until_close(monkeypatch, ole32, sounddevice, failure):
    engine = make_engine()
    monkeypatch.setattr(engine, '_get_api_name', lambda dev: 'ASIO')
    meter = Mock()
    monkeypatch.setattr(audio, '_AudioMeterWorker', lambda *args: meter)
    events = []
    ole32.CoUninitialize.side_effect = lambda: events.append('release')

    class Stream:
        def __init__(self, **kwargs):
            ole32.CoInitializeEx.assert_called_once_with(None, 2)
            if failure == 'open':
                raise RuntimeError('open failed')

        def start(self):
            ole32.CoUninitialize.assert_not_called()
            if failure == 'start':
                raise RuntimeError('start failed')

        def stop(self):
            if failure == 'stop':
                raise RuntimeError('stop failed')

        def close(self):
            ole32.CoUninitialize.assert_not_called()
            events.append('close')

    monkeypatch.setattr(sounddevice, 'Stream', Stream)
    if failure in ('open', 'start'):
        with pytest.raises(RuntimeError, match=failure):
            engine.play_sine(1000, -30, None)
    else:
        engine.play_sine(1000, -30, None)
        ole32.CoUninitialize.assert_not_called()
        engine.stop_sine()
    assert events == (['release'] if failure == 'open' else ['close', 'release'])
    assert engine._sine_stream is None and engine._sine_com_context is None
    engine.stop_sine()
    ole32.CoUninitialize.assert_called_once_with()


def test_device_lookup_never_restarts_portaudio(monkeypatch, sounddevice):
    # Recreating an Audio object resolves both saved device names. Those
    # lookups used to reset the library between successful ASIO captures.
    def forbidden():
        pytest.fail("Device lookup must not restart shared PortAudio")
    monkeypatch.setattr(sounddevice, '_terminate', forbidden)
    monkeypatch.setattr(sounddevice, '_initialize', forbidden)
    monkeypatch.setattr(sounddevice, 'query_hostapis', lambda: [{'name': 'ASIO'}])
    monkeypatch.setattr(sounddevice, 'query_devices', lambda: [dict(
        name='Test ASIO', hostapi=0, max_input_channels=2, max_output_channels=2)])
    for _ in range(3):
        assert audio.get_devices_and_channels()[0]['name'] == 'Test ASIO'
        assert audio.resolve_device_id_by_saved_identity(
            'Test ASIO', 'ASIO', role_label='input', require_input=True) == 0
