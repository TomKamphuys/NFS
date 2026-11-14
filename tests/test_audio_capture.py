import nfs.audio_capture as capture_io
"""Capture/DSP regressions using synthetic signals; no real hardware is opened."""

from dataclasses import replace

import configparser
import importlib
import json
import shutil
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

import nfs.audio as audio
from nfs.capture_detection import (CaptureContext, CaptureDetector, CaptureIssue, stream_health,
                                   loopback_gaps, microphone_gaps, psr_quality, thd_quality)
from nfs.audio import AlignmentEngine, MarkerGenerator, SweepGenerator, DeconvolutionEngine, HarmonicInjector, DSPUtils
from nfs.audio import AudioFactory, SINE_RMS_FROM_PEAK
from nfs.datatypes import CylindricalPosition
from nfs.nfs import NearFieldScanner

FS = 48000


@pytest.mark.parametrize('operation', ['sweep', 'finite_sine', 'continuous_sine'])
def test_live_meter_always_active(monkeypatch, operation):
    engine = make_engine()
    monkeypatch.setattr(engine.capture_engine, '_get_api_name', lambda dev: 'MME')
    meter = Mock()
    factory = Mock(return_value=meter)
    monkeypatch.setattr(capture_io, '_AudioMeterWorker', factory)
    prepared = engine._prepare_sweep()
    mic, loop = ideal_capture(prepared)
    count = len(mic) if operation == 'sweep' else 480
    inputs = np.column_stack((loop[:count], mic[:count]))
    outputs = np.full((count, 2), np.nan, dtype=np.float32)
    closed = []

    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs['callback']
            self.samplerate = FS
            self.latency = (0.01, 0.02)

        def start(self):
            self.callback(inputs, outputs, count, None, 'Input Overflow')

        def stop(self):
            pass

        def close(self):
            closed.append(True)

        def __enter__(self):
            self.start()
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(capture_io.sd, 'Stream', Stream)
    if operation == 'sweep':
        result = engine._run_sweep()
        np.testing.assert_array_equal(result['raw_recordings'][0], loop)
        np.testing.assert_array_equal(result['raw_recordings'][1], mic)
        np.testing.assert_array_equal(outputs[:, engine.hw['ch_out_spkr']], prepared['tx_speaker'])
        np.testing.assert_array_equal(outputs[:, engine.hw['ch_out_ref']], prepared['tx_loop'])
        assert result['capture_metadata']['stream_status'] == [
            {'sample': 0, 'status': 'Input Overflow'}]
        assert stream_health(CaptureContext(result, engine.hw["fs"], engine.cap)) is not None
    else:
        engine.play_sine(1000, -12, None if operation == 'continuous_sine' else 0.01)
        engine.stop_sine()
        expected = (np.sin(2 * np.pi * 1000 * np.arange(count) / FS)
                    * audio.DSPUtils.db_to_lin(-12)).astype(np.float32)
        for channel in range(2):
            np.testing.assert_allclose(outputs[:, channel], expected, atol=1e-7)
    assert closed == [True]
    factory.assert_called_once_with(FS)
    meter.start.assert_called_once()
    meter.submit.assert_called_once()
    meter.stop.assert_called_once()


# Capture sequence and exact replay

def make_engine(first=False, injected=False):
    fs = 48000
    hw = dict(
        dev_in=-1,
        dev_out=-1,
        ch_in_loop=0,
        ch_in_mic=1,
        ch_out_spkr=1,
        ch_out_ref=0,
        fs=fs,
        blocksize=0,
        wasapi_exclusive=False,
    )
    cap = dict(
        loopback_correction=False,  # Existing digital-only regressions.
        num_sweeps=3,
        sweep_dur_s=1.5,
        sweep_level_dbfs=-12,
        pre_sil_ms=200,
        post_sil_ms=100,
        debug_saves=True,
        naming_convention="dimitri",
        max_capture_retries=3,
        retry_check_stream_health=True,
        retry_check_loopback_silence=True,
        retry_check_mic_silence=True,
        retry_psr_threshold=3.0,
        retry_thd_threshold=10.0,
    )
    return audio.Audio(
        hw,
        cap,
        audio.SweepGenerator(fs, 1.5, 1, -12),
        audio.MarkerGenerator(fs, 100, (500, 5000), -12),
        audio.AlignmentEngine(fs, 3, first, 20, 100),
        audio.DeconvolutionEngine(fs),
        harmonic_injector=audio.HarmonicInjector(-40, -50) if injected else None,
    )

def ideal_capture(prepared, delays=(80, 83, 79), acoustic_delay=23):
    timing = prepared["timing"]
    mic = np.zeros(timing["total_samples"], dtype=np.float32)
    loop = np.zeros_like(mic)
    for start, delay in zip(timing["playback_marker_starts"], delays):
        count = timing["slot_samples"]
        loop[start + delay : start + delay + count] = prepared["tx_loop"][start : start + count]
        a = start + delay + acoustic_delay
        mic[a : a + count] = prepared["tx_speaker"][start : start + count]
    return (mic, loop)

def test_speaker_silent_during_markers_and_common_sweep_timing():
    e = make_engine()
    p = e._prepare_sweep()
    t = p["timing"]
    for start in t["playback_marker_starts"]:
        sweep_start = start + t["sweep_offset_samples"]
        assert np.count_nonzero(p["tx_speaker"][start:sweep_start]) == 0
        np.testing.assert_array_equal(p["tx_loop"][start : start + len(p["marker"])], p["marker"])
        assert np.count_nonzero(p["tx_loop"][start + len(p["marker"]) : sweep_start]) == 0
        np.testing.assert_array_equal(
            p["tx_loop"][sweep_start : sweep_start + len(p["speaker_sweep"])], p["speaker_sweep"]
        )
        np.testing.assert_array_equal(
            p["tx_speaker"][sweep_start : sweep_start + len(p["speaker_sweep"])], p["speaker_sweep"]
        )

@pytest.mark.parametrize(
    "first,delays", [(False, (80, 83, 79)), (True, (80, 80, 80)), (False, (0, 0, 0))]
)
def test_alignment_handles_marker_offset_and_preserves_acoustic_delay(first, delays):
    e = make_engine(first)
    p = e._prepare_sweep()
    mic, loop = ideal_capture(p, delays)
    r = e._finish_sweep(p, mic, loop)
    positions = r["capture_metadata"]["aligned_repeats"]
    assert len(positions) == 3
    for row, start, delay in zip(positions, p["timing"]["playback_marker_starts"], delays):
        assert row["marker_start_sample"] == start + delay
        assert row["sweep_start_sample"] == start + delay + p["timing"]["sweep_offset_samples"]
    expected = np.pad(p["speaker_sweep"], (23, p["timing"]["capture_tail_samples"] - 23))
    np.testing.assert_allclose(r["rx_mic_conditioned"], expected, atol=3e-08, rtol=0)
    expected_loop = np.pad(p["reference_sweep"], (0, p["timing"]["capture_tail_samples"]))
    if delays == (80, 83, 79):
        # The third marker arrives four samples early relative to repeat 2.
        # The full un-faded reference tail faithfully retains those samples.
        expected_loop[-4:] = p["marker"][:4] / 3
    np.testing.assert_allclose(r["rx_loop_aligned"], expected_loop, atol=3e-08, rtol=0)
    _, ir = e.deconv_engine.process_ir(r["rx_mic_conditioned"], r["inv_sweep"], inverse_origin_sample=r["capture_metadata"]["inverse_origin_sample"], sweep_samples=r["capture_metadata"]["sweep_samples"])
    assert np.argmax(abs(ir)) == audio._LINEAR_IR_ORIGIN_SAMPLE + 23
    r2 = e._finish_sweep(p, mic, loop * 0.4)
    np.testing.assert_array_equal(r2["inv_sweep"], r["inv_sweep"])
    np.testing.assert_array_equal(r2["rx_mic_conditioned"], r["rx_mic_conditioned"])

def test_injected_harmonics_do_not_enter_inverse_or_reference():
    plain = make_engine()._prepare_sweep()
    e = make_engine(injected=True)
    p = e._prepare_sweep()
    np.testing.assert_array_equal(p["inverse"], plain["inverse"])
    expected = (p["fundamental"] * p["timing"]["playback_gain"]).astype(np.float32)
    np.testing.assert_array_equal(p["reference_sweep"], expected)
    assert np.max(abs(p["speaker_sweep"] - p["reference_sweep"])) > 0.001

def test_truncated_last_capture_is_reported():
    e = make_engine()
    p = e._prepare_sweep()
    mic, loop = ideal_capture(p)
    t = p["timing"]
    cut = t["playback_marker_starts"][-1] + t["sweep_offset_samples"] + 80 + t["sweep_samples"] - 10
    with pytest.raises(RuntimeError):
        e._finish_sweep(p, mic[:cut], loop[:cut])

def test_debug_archive_reconstructs_exact_ir(monkeypatch, tmp_path):
    e = make_engine()
    p = e._prepare_sweep()
    mic, loop = ideal_capture(p)
    result = e._finish_sweep(p, mic, loop)
    monkeypatch.setattr(e, "_run_sweep", lambda: result)
    e.set_session_directory(tmp_path)
    exported = e.measure_ir(audio.CylindricalPosition(100, 0, 0))
    base = exported["name"].removesuffix("_ir.wav")
    debug = tmp_path / "debug"
    stored, fs = sf.read(debug / f"{base}_capture.wav", dtype="float32")
    assert fs == 48000 and sf.info(debug / f"{base}_capture.wav").subtype == "FLOAT"
    np.testing.assert_array_equal(stored, np.column_stack((loop, mic)))
    assert not list(debug.glob('*_capture_log.json'))
    meta = exported['capture_metadata']
    assert meta["raw_capture_channels"] == ["electrical_loopback", "microphone"]
    reference = np.load(debug / meta["reference_file"])
    np.testing.assert_array_equal(reference["inverse"], result["inv_sweep"])
    assert reference["inverse"].dtype == np.float64
    slices = [
        stored[row["sweep_start_sample"] : row["capture_end_sample"], 1]
        for row in meta["aligned_repeats"]
    ]
    averaged = audio.DSPUtils.hann_fade(np.mean(slices, axis=0), meta["mic_tail_fade_ms"], fs, side="out")
    _, rebuilt = e.deconv_engine.process_ir(averaged, reference["inverse"], inverse_origin_sample=meta["inverse_origin_sample"], sweep_samples=meta["sweep_samples"])
    np.testing.assert_array_equal(rebuilt, exported["ir_linear"])
    loop_slices = [stored[row["sweep_start_sample"]:row["capture_end_sample"], 0]
                   for row in meta["aligned_repeats"]]
    np.testing.assert_array_equal(np.mean(loop_slices, axis=0), result["rx_loop_aligned"])
    assert list(debug.glob("*.wav")) == [debug / f"{base}_capture.wav"]
    assert len(list((debug / "references").rglob("*.npz"))) == 1


def test_reference_archive_supports_fresh_marker_alignment(monkeypatch, tmp_path):
    engine = make_engine(injected=True)
    prepared = engine._prepare_sweep()
    mic, loop = ideal_capture(prepared)
    captured = engine._finish_sweep(prepared, mic, loop)
    monkeypatch.setattr(engine, '_run_sweep', lambda: captured)
    engine.set_session_directory(tmp_path)
    exported = engine.measure_ir(CylindricalPosition(100, 0, 0))
    archive = next((tmp_path / 'debug' / 'references').rglob('*.npz'))
    with np.load(archive, allow_pickle=False) as saved:
        metadata = json.loads(saved['metadata_json'].item())
        assert metadata['schema'] == 'shared_reference_v2'
        assert metadata['sample_rate'] == 48000
        assert metadata['raw_capture_channels'] == ['electrical_loopback', 'microphone']
        assert metadata['speaker_protection_hpf'] is None
        assert metadata['sweep']['log_sweep_rate_s'] == engine.sweep_gen.log_sweep_rate
        assert metadata['injected_harmonics_db']['H2'] is not None
        assert 'aligned_repeats' not in metadata
        layout = metadata['layout']
        assert 'linear_ir_samples' not in layout
        assert 'loopback_correction' not in metadata
        data, fs = sf.read(next((tmp_path / 'debug').glob('*_capture.wav')), dtype='float32')
        # Choose alignment policy afresh; do not reuse detected sample indices.
        alignment = AlignmentEngine(fs, metadata['num_sweeps'], False, 10,
                                    len(saved['marker']) * 1000 / fs)
        averaged, _, _, _ = alignment.sync_and_average(
            data[:, 1], data[:, 0], saved['marker'], layout['pre_samples'],
            layout['slot_samples'], layout['sweep_samples'],
            sweep_offset_samples=layout['sweep_offset_samples'],
            capture_tail_samples=layout['capture_tail_samples'])
        _, regenerated = DeconvolutionEngine(fs).process_ir(
            averaged, saved['inverse'],
            inverse_origin_sample=layout['inverse_origin_sample'],
            sweep_samples=layout['sweep_samples'])
        np.testing.assert_array_equal(regenerated, exported['ir_linear'])


def test_missing_marker_rejected():
    e = make_engine()
    p = e._prepare_sweep()
    mic, loop = ideal_capture(p)
    loop.fill(0)
    with pytest.raises(RuntimeError):
        e._finish_sweep(p, mic, loop)


# Known systems and Farina deconvolution

def extract(h, center, half=1200, nfft=8192):
    """Compare equal 50 ms windows at the predicted Farina arrival times."""
    center = round(center)
    z = np.zeros(2 * half)
    a = max(0, center - half)
    b = min(len(h), center + half)
    z[a - center + half : b - center + half] = h[a:b]
    z *= signal.windows.tukey(len(z), 0.2)
    return np.fft.rfft(z, nfft)

@pytest.fixture(scope="module")
def generated():
    gen = SweepGenerator(FS, 1.5, 1, -12)
    return (gen, *gen.generate())


@pytest.mark.parametrize("seconds", [0, float("nan"), float("inf")])
def test_reject_invalid_duration(seconds):
    with pytest.raises(ValueError):
        SweepGenerator(FS, seconds, 1, -12).generate()

def test_known_causal_system_including_echo_and_resonance(generated):
    gen, x, phase, inv = generated
    true = np.zeros(12000)
    true[100] = 1
    true[292] = 0.15
    t = np.arange(144) / FS
    true[100:244] += 0.03 * np.sin(2 * np.pi * 20000 * t) * np.exp(-t / 0.00035)
    true = signal.sosfilt(signal.butter(2, 80, fs=FS, btype="highpass", output="sos"), true)
    y = signal.fftconvolve(x, true)
    full, linear = DeconvolutionEngine(FS).process_ir(y, inv, inverse_origin_sample=gen.inverse_origin_sample, sweep_samples=round(gen.T * gen.fs))
    expected = true / np.sqrt(2)
    np.testing.assert_allclose(linear[audio._LINEAR_IR_ORIGIN_SAMPLE : audio._LINEAR_IR_ORIGIN_SAMPLE + 9600], expected[:9600], atol=4e-06, rtol=0)

@pytest.mark.parametrize("order,level", [(2, -40), (3, -50)])
def test_farina_harmonic_level_and_offset(generated, order, level):
    gen, x, phase, inv = generated
    mixed = HarmonicInjector(-40 if order == 2 else None, -50 if order == 3 else None).inject(
        x, phase, envelope=gen.fade_envelope, instantaneous_hz=gen.instantaneous_hz, fs=FS
    )
    full, _ = DeconvolutionEngine(FS).process_ir(np.pad(mixed, (0, 12000)), inv, inverse_origin_sample=gen.inverse_origin_sample, sweep_samples=round(gen.T * gen.fs))
    center = round(gen.T * gen.fs) - 1
    f = np.fft.rfftfreq(8192, 1 / FS)
    band = (f >= 1000) & (f <= 18000)
    reference = extract(full, center)
    expected = center - gen.log_sweep_rate * np.log(order) * FS
    harmonic = extract(full, expected)
    ratio = 20 * np.log10(abs(harmonic) / abs(reference))
    assert np.max(abs(ratio[band] - level)) < 0.01
    actual = (
        round(expected) - 10 + np.argmax(abs(full[round(expected) - 10 : round(expected) + 11]))
    )
    assert abs(actual - expected) < 2

def test_no_false_harmonics_in_linear_loopback(generated):
    gen, x, phase, inv = generated
    full, _ = DeconvolutionEngine(FS).process_ir(np.pad(x, (0, 12000)), inv, inverse_origin_sample=gen.inverse_origin_sample, sweep_samples=round(gen.T * gen.fs))
    a = round(gen.T * gen.fs) - 1
    f = np.fft.rfftfreq(8192, 1 / FS)
    band = (f >= 1000) & (f <= 18000)
    ref = extract(full, a)
    for order in (2, 3):
        residual = extract(full, a - gen.log_sweep_rate * np.log(order) * FS)
        assert np.max(abs(residual[band]) / abs(ref[band])) < 10 ** (-100 / 20)

def test_padding_and_long_late_echo_do_not_wrap(generated):
    gen, x, phase, inv = generated
    a = np.pad(x, (0, 12000))
    b = np.pad(a, (0, 100000))
    echo_start = 90000
    b[echo_start : echo_start + len(x)] += 0.2 * x
    _, short = DeconvolutionEngine(FS).process_ir(a, inv, inverse_origin_sample=gen.inverse_origin_sample, sweep_samples=round(gen.T * gen.fs))
    _, long = DeconvolutionEngine(FS).process_ir(b, inv, inverse_origin_sample=gen.inverse_origin_sample, sweep_samples=round(gen.T * gen.fs))
    np.testing.assert_allclose(short[:9650], long[:9650], atol=1e-07, rtol=0)

def test_float32_playback_preserves_precision(generated):
    gen, x, phase, inv = generated
    _, ir = DeconvolutionEngine(FS).process_ir(np.pad(x.astype(np.float32), (0, 12000)), inv, inverse_origin_sample=gen.inverse_origin_sample, sweep_samples=round(gen.T * gen.fs))
    expected = np.zeros(10000)
    expected[audio._LINEAR_IR_ORIGIN_SAMPLE] = 1
    expected = expected / np.sqrt(2)
    np.testing.assert_allclose(ir[:10000], expected, atol=4e-06, rtol=0)


# Marker alignment quality

def marker_recording():
    marker = MarkerGenerator(FS, 100, (500, 5000), -6).generate()
    raw = np.zeros(FS)
    raw[24000:24000 + len(marker)] = marker
    return raw, marker, AlignmentEngine(FS, 1, False, 20, 100)

def test_psr_is_finite_and_independent_of_peak_at_search_boundary():
    raw, marker, align = marker_recording()
    results = [align._matched_filter_detect(raw, marker, a, b, normalize=True)
               for a, b in ((23760, 24240), (24000, 24480), (23520, 24000))]
    assert [r[0] for r in results] == [24000] * 3
    assert np.isfinite(results[0][2]) and results[0][2] > 0
    np.testing.assert_allclose([r[2] for r in results], results[0][2], rtol=1e-12)

def test_competing_marker_reduces_psr_without_changing_selected_lag():
    raw, marker, align = marker_recording()
    clean = align._matched_filter_detect(raw, marker, 23760, 24240, normalize=True)
    raw[24900:24900 + len(marker)] += .65 * marker
    ambiguous = align._matched_filter_detect(raw, marker, 23760, 24240, normalize=True)
    assert clean[0] == ambiguous[0] == 24000
    assert np.isfinite(ambiguous[2])
    assert ambiguous[2] < clean[2]


# Room decay and export length

@pytest.mark.parametrize('post_ms', [0, 5, 500, 2000])
def test_post_interval_is_retained_once_without_an_extra_wait(post_ms):
    e = make_engine()
    e.cap['post_sil_ms'] = post_ms
    p = e._prepare_sweep()
    t = p['timing']
    post = round(post_ms * 48)
    assert t['capture_tail_samples'] == t['post_samples'] == t['flush_samples'] == post
    assert t['total_samples'] == t['pre_samples'] + e.cap['num_sweeps'] * t['slot_samples'] + post
    assert t['slot_samples'] == t['sweep_offset_samples'] + t['sweep_samples'] + post
    for a in t['playback_marker_starts']:
        end = a + t['sweep_offset_samples'] + t['sweep_samples']
        assert not np.any(p['tx_speaker'][end:end+post])
        assert not np.any(p['tx_loop'][end:end+post])
    mic, loop = ideal_capture(p, delays=(0,0,0), acoustic_delay=0)
    result = e._finish_sweep(p,mic,loop)
    expected = np.pad(p['speaker_sweep'], (0,post))
    if post:
        expected = DSPUtils.hann_fade(expected, min(10,post_ms),48000,side='out')
    np.testing.assert_allclose(result['rx_mic_conditioned'],expected,atol=3e-8,rtol=0)
    assert len(result['rx_mic_conditioned']) == t['sweep_samples'] + post

@pytest.mark.parametrize('post_ms,last_echo', [(500,.45), (2000,1.8)])
def test_room_echoes_survive_capture_deconvolution_and_export(post_ms,last_echo,monkeypatch):
    e = make_engine()
    e.cap.update(post_sil_ms=post_ms,debug_saves=False)
    p = e._prepare_sweep()
    fs = 48000
    room = np.zeros(round((last_echo+.04)*fs))
    room[0] = 1
    for seconds, amplitude in ((.1,.25),(.3,.15),(last_echo,.1)):
        room[round(seconds*fs)] += amplitude
    # A real decaying band-limited tail, in addition to isolated echoes.
    t = np.arange(round(.25*fs))/fs
    room[round(.05*fs):round(.05*fs)+len(t)] += .001*np.sin(2*np.pi*700*t)*np.exp(-t/.04)
    mic0, loop = ideal_capture(p,delays=(80,80,80),acoustic_delay=0)
    mic = signal.fftconvolve(mic0,room)[:len(mic0)].astype(np.float32)
    r = e._finish_sweep(p,mic,loop)
    monkeypatch.setattr(e,'_run_sweep',lambda:r)
    from nfs.audio import CylindricalPosition
    result = e.measure_ir(CylindricalPosition(0,0,0),save=False)
    ir = result['ir_linear']
    expected = room.copy()
    expected *= p['timing']['playback_gain']/np.sqrt(2)
    np.testing.assert_allclose(ir[audio._LINEAR_IR_ORIGIN_SAMPLE:audio._LINEAR_IR_ORIGIN_SAMPLE+len(room)],expected,atol=5e-6,rtol=0)
    for seconds in (.1,.3,last_echo):
        k = 50+round(seconds*fs)
        assert np.argmax(abs(ir[k-3:k+4])) == 3

@pytest.mark.parametrize('channel', ['mic','loop'])
def test_missing_final_decay_samples_are_rejected(channel):
    e = make_engine()
    e.cap['post_sil_ms'] = 500
    p = e._prepare_sweep()
    mic,loop = ideal_capture(p,delays=(80,80,80))
    t = p['timing']
    end = t['playback_marker_starts'][-1]+80+t['sweep_offset_samples']+t['sweep_samples']+t['post_samples']
    if channel == 'mic':
        mic = mic[:end-1]
    else:
        loop = loop[:end-1]
    with pytest.raises(RuntimeError):
        e._finish_sweep(p,mic,loop)


# Reference archives and app sessions

def setup_capture(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    engine = make_engine()
    prepared = engine._prepare_sweep()
    mic, loop = ideal_capture(prepared)
    result = engine._finish_sweep(prepared, mic, loop)
    monkeypatch.setattr(engine, '_run_sweep', lambda: result)
    return engine, result

def test_scan_shares_reference_and_single_measurements_start_new_sessions(monkeypatch, tmp_path):
    engine, captured = setup_capture(monkeypatch, tmp_path)
    metadata = []
    measure_ir = engine.measure_ir
    def record_measurement(*args, **kwargs):
        result = measure_ir(*args, **kwargs)
        metadata.append(dict(result['capture_metadata']))
        return result
    monkeypatch.setattr(engine, 'measure_ir', record_measurement)
    scanner, motion = Mock(), Mock()
    scanner.get_position.side_effect = [CylindricalPosition(r, 0, 0) for r in (100, 200, 300, 400)]
    motion.ready.side_effect = [False, False, True]
    motion.total_points.return_value = 2
    app = NearFieldScanner(scanner, engine, motion)
    app.set_project_directory(tmp_path)
    app.take_measurement_set()
    scan = tmp_path/'measurement_set'/'debug'
    assert not list(scan.glob('*_capture_log.json'))
    assert len(metadata) == 2
    assert metadata[0]['reference_file'] == metadata[1]['reference_file']
    archives = list(scan.rglob('*.npz'))
    assert len(archives) == 1
    with np.load(archives[0]) as saved:
        assert set(saved.files) == set(captured['digital_reference']) | {'metadata_json'}
        for key, value in captured['digital_reference'].items():
            np.testing.assert_array_equal(saved[key], value)
            assert saved[key].dtype == value.dtype
    assert len(list(scan.glob('*.wav'))) == 2  # Only the two raw stereo captures.
    assert not list(scan.glob('*_reference.npz'))

    app.take_single_measurement()
    app.take_single_measurement()
    singles = tmp_path/'single_measurements'/'debug'
    single_meta = metadata[2:]
    assert not list(singles.glob('*_capture_log.json'))
    assert len(single_meta) == 2
    assert len(list(singles.rglob('*.npz'))) == 2
    assert single_meta[0]['reference_session_id'] != single_meta[1]['reference_session_id']
    assert single_meta[0]['reference_id'] == single_meta[1]['reference_id']
    assert single_meta[0]['reference_session_id'] != metadata[0]['reference_session_id']

    # Relative links still resolve after the entire archive is relocated.
    moved = tmp_path/'moved_debug'
    shutil.copytree(scan, moved)
    for row in metadata[:2]:
        assert (moved/row['reference_file']).read_bytes() == archives[0].read_bytes()

def test_equal_references_are_written_once_but_changed_data_get_new_archive(monkeypatch, tmp_path):
    engine = make_engine()
    engine.set_session_directory(tmp_path)
    arrays = {'fundamental': np.array([0., 1., 0.]), 'inverse': np.array([0., 1., 0.])}
    import nfs.audio as audio
    writer = Mock(wraps=audio.np.savez_compressed)
    monkeypatch.setattr(audio.np, 'savez_compressed', writer)
    a = engine._save_shared_reference(arrays)
    before = (engine.debug_dir/a['reference_file']).read_bytes()
    for _ in range(10):
        # Equal values, not merely the same array identities, must share.
        assert engine._save_shared_reference({k:v.copy() for k,v in arrays.items()}) == a
    assert writer.call_count == 1
    arrays['inverse'][1] = .5
    b = engine._save_shared_reference(arrays)
    assert b['reference_id'] != a['reference_id']
    assert b['reference_session_id'] == a['reference_session_id']
    assert (engine.debug_dir/a['reference_file']).read_bytes() == before
    engine.hw['fs'] = 96000
    c = engine._save_shared_reference(arrays)
    assert c['reference_id'] != b['reference_id']
    assert writer.call_count == 3
    d = engine._save_shared_reference(arrays, {'num_sweeps': 2})
    assert d['reference_id'] != c['reference_id']
    assert engine._save_shared_reference(arrays, {'num_sweeps': 2}) == d
    assert writer.call_count == 4

@pytest.mark.parametrize('save,debug', [(False, True), (True, False)])
def test_unsaved_or_archive_disabled_does_not_write_reference(monkeypatch,tmp_path,save,debug):
    engine, _ = setup_capture(monkeypatch,tmp_path)
    engine.cap['debug_saves'] = debug
    engine.set_session_directory(tmp_path)
    engine.measure_ir(CylindricalPosition(100,0,0),save=save)
    assert not list(tmp_path.rglob('*.npz'))
    assert not list(tmp_path.rglob('*_capture.wav'))

def test_failed_reference_write_does_not_publish_partial_archive(monkeypatch,tmp_path):
    engine = make_engine()
    engine.set_session_directory(tmp_path)
    import nfs.audio as audio
    def fail(handle, **arrays):
        handle.write(b'incomplete archive')
        raise OSError('simulated disk failure')
    monkeypatch.setattr(audio.np,'savez_compressed',fail)
    with pytest.raises(OSError):
        engine._save_shared_reference({'inverse': np.ones(8)})
    assert not [p for p in (tmp_path/'debug').rglob('*') if p.is_file()]


# End-to-end mock-interface export regression

@pytest.fixture
def mock_config(tmp_path):
    config = configparser.ConfigParser()
    config["audio"] = {
        "mode": "mock_interface",
        "fs": "48000",
        "in_dev": "0",
        "out_dev": "0",
        "in_ch_mic": "1",
        "in_ch_loop": "0",
        "out_ch_spkr": "0",
        "out_ch_ref": "1",
        "wasapi_exclusive": "False",
    }
    config["sweep"] = {
        "sweep_dur_s": "0.5",
        "sweep_level_dbfs": "-10",
        "num_sweeps": "1",
        "pre_sil_ms": "100",
        "post_sil_ms": "100",
        "mic_tail_taper_ms": "10",
        "align_to_first_marker": "True",
        "debug_saves": "True",
        "H2_TEST_DB": "None",
        "H3_TEST_DB": "None",
        "protect_hpf_hz": "0",
        "protect_hpf_order": "4",
        "protect_hpf_correction": "False",
        "protect_hpf_corr_db_cap": "12.0",
        "loopback_correction": "False",  # Known uncorrected mock transfer.
        "ir_highpass_5hz": "True",  # Obsolete setting is ignored.
    }

    config_path = tmp_path / "regression_test_config.ini"
    with open(config_path, "w") as f:
        config.write(f)
    return config_path


def test_legacy_inverse_layout_and_explicit_metadata_agree():
    inverse = np.zeros(1000); inverse[999] = 1
    recorded = np.zeros(1500); recorded[5] = 1
    engine = DeconvolutionEngine(48000)
    legacy = engine.process_ir(recorded, inverse)
    explicit = engine.process_ir(recorded, inverse, inverse_origin_sample=999, sweep_samples=1000)
    for a, b in zip(legacy, explicit):
        np.testing.assert_array_equal(a, b)
    assert np.argmax(abs(legacy[1])) == audio._LINEAR_IR_ORIGIN_SAMPLE + 5


@pytest.mark.parametrize('kw', [dict(inverse_origin_sample=100),
    dict(inverse_origin_sample=100, sweep_samples=200),
    dict(inverse_origin_sample=1000, sweep_samples=100),
    dict(inverse_origin_sample=99.5, sweep_samples=100)])
def test_invalid_inverse_timing_rejected(kw):
    with pytest.raises(ValueError):
        DeconvolutionEngine(48000).process_ir(np.ones(1000), np.ones(1000), **kw)


# Dropout detection helpers and retry loop

def test_check_stream_health_detects_underflow(monkeypatch):
    log = Mock()
    monkeypatch.setattr(importlib.import_module(stream_health.__module__), 'logger', log)
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    result = {
        'capture_metadata': {
            'stream_status': [
                {'sample': 1000, 'status': 'InputUnderflow'},
            ]
        },
        'raw_recordings': (np.ones(1000), np.ones(1000)),
    }
    assert stream_health(CaptureContext(result, e.hw["fs"], e.cap)) is not None
    message = log.warning.call_args.args[0]
    assert 'InputUnderflow' in message
    assert 'sample 1000' in message


def test_check_stream_health_detects_overflow():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    result = {
        'capture_metadata': {
            'stream_status': [
                {'sample': 2000, 'status': 'OutputOverflow'},
            ]
        },
        'raw_recordings': (np.ones(1000), np.ones(1000)),
    }
    assert stream_health(CaptureContext(result, e.hw["fs"], e.cap)) is not None


def test_check_stream_health_clean_when_no_flags():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    result = {
        'capture_metadata': {
            'stream_status': []
        },
        'raw_recordings': (np.ones(1000), np.ones(1000)),
    }
    assert stream_health(CaptureContext(result, e.hw["fs"], e.cap)) is None


def test_check_stream_health_clean_when_empty_status():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    result = {
        'capture_metadata': {},
        'raw_recordings': (np.ones(1000), np.ones(1000)),
    }
    assert stream_health(CaptureContext(result, e.hw["fs"], e.cap)) is None


def test_check_silence_gaps_detects_gap_in_loopback(monkeypatch):
    log = Mock()
    monkeypatch.setattr(importlib.import_module(loopback_gaps.__module__), 'logger', log)
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    fs = 48000
    guard_samples = 480  # 10 ms guard
    sweep_samples = 72000  # 1.5 s sweep
    sweep_start = 10000  # arbitrary position
    total = sweep_start + sweep_samples + 2000
    loopback = np.ones(total, dtype=np.float32) * 0.5
    # Insert a silence gap inside the active sweep portion (after guard)
    gap_start = sweep_start + guard_samples + 10000
    gap_end = gap_start + 2000  # ~40 ms of silence — wider than window size
    loopback[gap_start:gap_end] = 0.0
    result = {
        'capture_metadata': {
            'sweep_samples': sweep_samples,
            'guard_samples': guard_samples,
            'aligned_repeats': [
                {'sweep_start_sample': sweep_start},
            ],
        },
        'raw_recordings': (loopback, np.ones(total, dtype=np.float32)),
    }
    assert loopback_gaps(CaptureContext(result, e.hw["fs"], e.cap)) is not None
    message = log.warning.call_args.args[0]
    assert 'Loopback silence gap' in message
    assert 'window_start_sample=' in message
    assert 'rms=' in message
    assert 'threshold=' in message


def test_unreliable_marker_logs_search_and_loopback_levels(monkeypatch):
    log = Mock()
    monkeypatch.setattr(importlib.import_module(AlignmentEngine.__module__), 'logger', log)
    e = make_engine()
    marker = np.ones(16, dtype=np.float32)

    with pytest.raises(RuntimeError, match='normalised correlation 0.000'):
        e.alignment_engine._matched_filter_detect(
            np.zeros(128, dtype=np.float32), marker,
            search_start=5, search_end=64, normalize=True,
        )

    message = log.warning.call_args.args[0]
    assert 'normalized_correlation=0.000' in message
    assert 'candidate_sample=' in message
    assert 'search_range=[5, 64]' in message
    assert 'loopback_peak=0' in message
    assert 'loopback_rms=0' in message


def test_check_silence_gaps_clean_loopback():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    fs = 48000
    guard_samples = 480
    sweep_samples = 72000
    sweep_start = 10000
    total = sweep_start + sweep_samples + 2000
    loopback = np.ones(total, dtype=np.float32) * 0.5
    result = {
        'capture_metadata': {
            'sweep_samples': sweep_samples,
            'guard_samples': guard_samples,
            'aligned_repeats': [
                {'sweep_start_sample': sweep_start},
            ],
        },
        'raw_recordings': (loopback, np.ones(total, dtype=np.float32)),
    }
    assert loopback_gaps(CaptureContext(result, e.hw["fs"], e.cap)) is None


def test_check_silence_gaps_ignores_silence_in_pre_post_region():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    fs = 48000
    guard_samples = 480
    sweep_samples = 72000
    sweep_start = 10000
    total = sweep_start + sweep_samples + 2000
    loopback = np.ones(total, dtype=np.float32) * 0.5
    # Silence before the sweep should not trigger
    loopback[0:sweep_start] = 0.0
    # Silence in guard samples should not trigger
    loopback[sweep_start:sweep_start + guard_samples] = 0.0
    loopback[sweep_start + sweep_samples - guard_samples:sweep_start + sweep_samples] = 0.0
    # Silence after the sweep should not trigger
    loopback[sweep_start + sweep_samples:total] = 0.0
    result = {
        'capture_metadata': {
            'sweep_samples': sweep_samples,
            'guard_samples': guard_samples,
            'aligned_repeats': [
                {'sweep_start_sample': sweep_start},
            ],
        },
        'raw_recordings': (loopback, np.ones(total, dtype=np.float32)),
    }
    assert loopback_gaps(CaptureContext(result, e.hw["fs"], e.cap)) is None


def test_check_mic_silence_gaps_detects_sudden_drop(monkeypatch):
    """A sudden silence gap in an otherwise strong mic signal is detected."""
    log = Mock()
    monkeypatch.setattr(importlib.import_module(microphone_gaps.__module__), 'logger', log)
    e = make_engine()
    fs = 48000
    guard_samples = 480
    sweep_samples = 72000
    sweep_start = 10000
    total = sweep_start + sweep_samples + 2000
    loopback = np.ones(total, dtype=np.float32) * 0.5
    mic = np.ones(total, dtype=np.float32) * 0.3
    # Insert a silence gap inside the active sweep portion
    gap_start = sweep_start + guard_samples + 10000
    gap_end = gap_start + 2000  # ~40 ms of silence
    mic[gap_start:gap_end] = 0.0
    result = {
        'capture_metadata': {
            'sweep_samples': sweep_samples,
            'guard_samples': guard_samples,
            'aligned_repeats': [
                {'sweep_start_sample': sweep_start},
            ],
        },
        'raw_recordings': (loopback, mic),
    }
    assert microphone_gaps(CaptureContext(result, e.hw["fs"], e.cap)) is not None
    message = log.warning.call_args.args[0]
    assert 'Microphone silence gap' in message


def test_check_mic_silence_gaps_passes_low_signal():
    """A consistently low-signal mic recording (behind the speaker) passes without false positive."""
    e = make_engine()
    fs = 48000
    guard_samples = 480
    sweep_samples = 72000
    sweep_start = 10000
    total = sweep_start + sweep_samples + 2000
    loopback = np.ones(total, dtype=np.float32) * 0.5
    # Mic signal is uniformly low — simulating a position behind the speaker
    mic = np.ones(total, dtype=np.float32) * 0.001
    result = {
        'capture_metadata': {
            'sweep_samples': sweep_samples,
            'guard_samples': guard_samples,
            'aligned_repeats': [
                {'sweep_start_sample': sweep_start},
            ],
        },
        'raw_recordings': (loopback, mic),
    }
    assert microphone_gaps(CaptureContext(result, e.hw["fs"], e.cap)) is None


def test_check_mic_silence_gaps_passes_gradual_sweep():
    """A sweep with gradually varying energy (no sudden drops) passes without false positive."""
    e = make_engine()
    fs = 48000
    guard_samples = 480
    sweep_samples = 72000
    sweep_start = 10000
    total = sweep_start + sweep_samples + 2000
    loopback = np.ones(total, dtype=np.float32) * 0.5
    # Create a gradual sine-modulated envelope — energy varies smoothly
    t = np.linspace(0, 1.5, sweep_samples)
    envelope = 0.1 + 0.2 * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * t))
    mic = np.zeros(total, dtype=np.float32)
    check_start = sweep_start + guard_samples
    check_end = sweep_start + sweep_samples - guard_samples
    sweep_t = np.linspace(0, 1.5, check_end - check_start)
    sweep_envelope = 0.1 + 0.2 * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * sweep_t))
    mic[check_start:check_end] = sweep_envelope * np.sin(2 * np.pi * 1000 * sweep_t)
    result = {
        'capture_metadata': {
            'sweep_samples': sweep_samples,
            'guard_samples': guard_samples,
            'aligned_repeats': [
                {'sweep_start_sample': sweep_start},
            ],
        },
        'raw_recordings': (loopback, mic),
    }
    assert microphone_gaps(CaptureContext(result, e.hw["fs"], e.cap)) is None


def test_retry_loop_retries_on_mic_silence_gap(tmp_path, monkeypatch):
    """The capture retry loop catches mic silence gaps and retries."""
    e = make_engine()
    e.cap['max_capture_retries'] = 3

    attempts = [0]
    def run_sweep():
        attempts[0] += 1
        capture = _make_archivable_capture(e, [])
        if attempts[0] < 3:
            # First two attempts: inject a silence gap in the mic channel
            mic = np.array(capture['raw_recordings'][1], dtype=np.float32, copy=True)
            metadata = capture['capture_metadata']
            sweep_start = metadata['aligned_repeats'][0]['sweep_start_sample']
            guard = metadata['guard_samples']
            gap_start = sweep_start + guard + 10000
            mic[gap_start:gap_start + 2000] = 0.0
            capture['raw_recordings'] = (capture['raw_recordings'][0], mic)
        else:
            # Third attempt: replace mic with a uniform signal so no gap is detected
            mic = np.ones(len(capture['raw_recordings'][1]), dtype=np.float32) * 0.3
            capture['raw_recordings'] = (capture['raw_recordings'][0], mic)
        return capture

    monkeypatch.setattr(e, '_run_sweep', run_sweep)

    ir_path = tmp_path / 'test_ir.wav'
    dist_path = tmp_path / 'test_dist.wav'
    result = e.capture_ir(ir_path, dist_path, save=False)

    assert attempts[0] == 3  # Retried twice, succeeded on third
    assert result['ir_linear'] is not None


def test_check_quality_flags_low_psr():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    metrics = {'psr': 1.5, 'snr_db': 60.0}
    assert psr_quality(CaptureContext({}, e.hw["fs"], e.cap, metrics)) is not None


def test_check_quality_passes_high_psr():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    metrics = {'psr': 5.0, 'snr_db': 60.0}
    assert psr_quality(CaptureContext({}, e.hw["fs"], e.cap, metrics)) is None


def test_check_quality_ignores_low_snr():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    # Low SNR should NOT trigger retry — only PSR matters
    metrics = {'psr': 5.0, 'snr_db': 10.0}
    assert psr_quality(CaptureContext({}, e.hw["fs"], e.cap, metrics)) is None


def test_check_quality_uses_configurable_threshold():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 5.0
    metrics = {'psr': 4.0, 'snr_db': 60.0}
    assert psr_quality(CaptureContext({}, e.hw["fs"], e.cap, metrics)) is not None
    metrics = {'psr': 6.0, 'snr_db': 60.0}
    assert psr_quality(CaptureContext({}, e.hw["fs"], e.cap, metrics)) is None


def test_retry_loop_succeeds_on_clean_capture(monkeypatch):
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    p = e._prepare_sweep()
    mic, loop = ideal_capture(p)
    result = e._finish_sweep(p, mic, loop)
    call_count = [0]

    def mock_run_sweep():
        call_count[0] += 1
        return result

    monkeypatch.setattr(e, '_run_sweep', mock_run_sweep)
    exported = e.measure_ir(CylindricalPosition(100, 0, 0), save=False)
    assert call_count[0] == 1
    assert exported['ir_linear'] is not None
    assert 'error' not in exported


def test_retry_loop_retries_on_stream_health_failure(monkeypatch):
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    p = e._prepare_sweep()
    mic, loop = ideal_capture(p)
    result = e._finish_sweep(p, mic, loop)
    call_count = [0]

    def mock_run_sweep():
        call_count[0] += 1
        if call_count[0] < 2:
            # First attempt has underflow
            return {
                'capture_metadata': {
                    'stream_status': [{'sample': 100, 'status': 'InputUnderflow'}],
                    'sweep_samples': p['timing']['sweep_samples'],
                },
                'raw_recordings': (np.ones(1000), np.ones(1000)),
            }
        return result

    monkeypatch.setattr(e, '_run_sweep', mock_run_sweep)
    exported = e.measure_ir(CylindricalPosition(100, 0, 0), save=False)
    assert call_count[0] == 2
    assert exported['ir_linear'] is not None
    assert 'error' not in exported


def test_retry_loop_retries_on_silence_gap(monkeypatch):
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    p = e._prepare_sweep()
    mic, loop = ideal_capture(p)
    result = e._finish_sweep(p, mic, loop)
    call_count = [0]
    meta = result['capture_metadata']
    sweep_start = meta['aligned_repeats'][0]['sweep_start_sample']

    def mock_run_sweep():
        call_count[0] += 1
        if call_count[0] < 2:
            # First attempt has silence gap in loopback sweep portion
            loopback = loop.copy()
            # Zero out a 10 ms window inside the sweep portion
            gap_start = sweep_start + 1000
            gap_end = gap_start + 2000  # ~40 ms of silence — wider than window size
            loopback[gap_start:gap_end] = 0.0
            return {
                'capture_metadata': dict(meta, stream_status=[]),
                'raw_recordings': (loopback, mic.copy()),
            }
        return result

    monkeypatch.setattr(e, '_run_sweep', mock_run_sweep)
    exported = e.measure_ir(CylindricalPosition(100, 0, 0), save=False)
    assert call_count[0] == 2
    assert exported['ir_linear'] is not None
    assert 'error' not in exported


def test_retry_loop_exhausts_retries_and_returns_failure(monkeypatch):
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    call_count = [0]

    def mock_run_sweep():
        call_count[0] += 1
        return {
            'capture_metadata': {
                'stream_status': [{'sample': 100, 'status': 'InputUnderflow'}],
                'sweep_samples': 1000,
            },
            'raw_recordings': (np.ones(1000), np.ones(1000)),
        }

    monkeypatch.setattr(e, '_run_sweep', mock_run_sweep)
    exported = e.measure_ir(CylindricalPosition(100, 0, 0), save=False)
    assert call_count[0] == 3
    assert exported['ir_linear'] is None
    assert 'error' in exported
    assert 'after 3 attempts' in exported['error'].lower()
    assert 'underflow/overflow' in exported['error'].lower()


def test_capture_logs_sweep_exception_and_propagates(tmp_path, monkeypatch):
    e = make_engine()
    log = Mock()
    monkeypatch.setattr(audio, 'logger', log)

    def fail_sweep():
        raise RuntimeError('marker alignment failed')

    monkeypatch.setattr(e, '_run_sweep', fail_sweep)
    with pytest.raises(RuntimeError, match='marker alignment failed'):
        e.capture_ir(tmp_path / 'measurement_ir.wav', tmp_path / 'measurement_dist.wav', save=False)

    message = log.exception.call_args.args[0]
    assert 'measurement_ir.wav' in message
    assert 'attempt 1/3' in message


def test_quality_rejection_logs_configured_threshold(tmp_path, monkeypatch):
    e = make_engine()
    e.cap['max_capture_retries'] = 1
    e.cap['retry_psr_threshold'] = 4.0
    log = Mock()
    monkeypatch.setattr(audio, 'logger', log)
    monkeypatch.setattr(e, '_run_sweep', lambda: {'capture_metadata': {'sweep_samples': 100}})
    monkeypatch.setattr(e, '_process_capture', lambda result: (np.zeros(1), np.zeros(1)))
    monkeypatch.setattr(e.verifier, 'calculate_metrics', lambda *args, **kwargs: {
        'psr': 2.5,
        'snr_db': 0.0,
    })

    e.capture_ir(tmp_path / 'measurement_ir.wav', tmp_path / 'measurement_dist.wav', save=False)

    message = next(call.args[0] for call in log.warning.call_args_list
                   if 'quality below threshold' in call.args[0])
    assert 'PSR=2.50' in message
    assert 'threshold=4.00' in message


def test_alignment_failure_retains_current_capture_context():
    e = make_engine()
    prepared = e._prepare_sweep()
    rec_mic = np.linspace(-0.25, 0.25, prepared['timing']['total_samples'], dtype=np.float32)
    rec_loop = np.linspace(0.5, -0.5, prepared['timing']['total_samples'], dtype=np.float32)

    with pytest.raises(audio.CaptureAlignmentError) as failure:
        e._finish_sweep(prepared, rec_mic, rec_loop)

    context = failure.value.capture_context
    np.testing.assert_array_equal(context['raw_recordings'][0], rec_loop)
    np.testing.assert_array_equal(context['raw_recordings'][1], rec_mic)
    for key in ('fundamental', 'inverse', 'speaker_sweep', 'loopback_sweep', 'marker'):
        np.testing.assert_array_equal(context['digital_reference'][key], {
            'fundamental': prepared['fundamental'],
            'inverse': prepared['inverse'],
            'speaker_sweep': prepared['speaker_sweep'],
            'loopback_sweep': prepared['reference_sweep'],
            'marker': prepared['marker'],
        }[key])
    metadata = context['capture_metadata']
    assert metadata['playback_marker_starts'] == prepared['timing']['playback_marker_starts']
    assert metadata['aligned_repeats'] == []


def test_capture_retries_alignment_error_then_returns_success(tmp_path, monkeypatch):
    e = make_engine()
    prepared = e._prepare_sweep()
    mic, loop = ideal_capture(prepared)
    success = e._finish_sweep(prepared, mic, loop)
    calls = []

    def run_sweep():
        calls.append(True)
        if len(calls) == 1:
            raise audio.CaptureAlignmentError('later marker was not found')
        return success

    monkeypatch.setattr(e, '_run_sweep', run_sweep)
    monkeypatch.setattr(e, '_process_capture', lambda result: (np.zeros(1), np.zeros(1)))
    monkeypatch.setattr(e.verifier, 'calculate_metrics', lambda *args, **kwargs: {
        'psr': 5.0,
        'snr_db': 0.0,
        'thd_pct': 0.0,
        'psr_db': 0.0,
        'psr_reference_db': 0.0,
        'alignment_score_10': 0.0,
    })

    result = e.capture_ir(tmp_path / 'measurement_ir.wav', tmp_path / 'measurement_dist.wav', save=False)

    assert len(calls) == 2
    assert result['ir_linear'] is not None
    assert result['retries'] == 1


def test_capture_alignment_exhaustion_reports_reason_and_attempt_count(tmp_path, monkeypatch):
    e = make_engine()
    e.cap['max_capture_retries'] = 2
    calls = []

    def fail_alignment():
        calls.append(True)
        raise audio.CaptureAlignmentError('later marker was not found')

    monkeypatch.setattr(e, '_run_sweep', fail_alignment)

    result = e.capture_ir(tmp_path / 'measurement_ir.wav', tmp_path / 'measurement_dist.wav', save=False)

    assert len(calls) == 2
    assert result['ir_linear'] is None
    assert 'after 2 attempts' in result['error']
    assert 'later marker was not found' in result['error']


def test_capture_propagates_unrelated_sweep_runtime_error(tmp_path, monkeypatch):
    e = make_engine()
    monkeypatch.setattr(e, '_run_sweep', Mock(side_effect=RuntimeError('audio device failure')))

    with pytest.raises(RuntimeError, match='audio device failure'):
        e.capture_ir(tmp_path / 'measurement_ir.wav', tmp_path / 'measurement_dist.wav', save=False)


def _make_archivable_capture(engine, stream_status=None):
    prepared = engine._prepare_sweep()
    mic, loop = ideal_capture(prepared, acoustic_delay=31)
    capture = engine._finish_sweep(prepared, mic, loop)
    capture['capture_metadata'].update(
        actual_sample_rate=FS,
        stream_latency_s=0.012,
        stream_status=stream_status or [],
    )
    return capture


@pytest.mark.parametrize('rejection', ['stream', 'silence', 'psr'])
def test_rejected_capture_archive_preserves_raw_channels_and_reference(tmp_path, monkeypatch, rejection):
    e = make_engine()
    e.cap['max_capture_retries'] = 1
    session_dir = tmp_path / 'session'
    e.set_session_directory(session_dir)
    status = [{'sample': 1234, 'status': 'InputUnderflow'}] if rejection == 'stream' else []
    capture = _make_archivable_capture(e, status)
    monkeypatch.setattr(e, '_run_sweep', lambda: capture)

    if rejection == 'silence':
        _replace_loopback_detector(e, lambda context: CaptureIssue('loopback_gaps', 'silence gap in loopback'))
    elif rejection == 'psr':
        _replace_loopback_detector(e, lambda context: None)
        monkeypatch.setattr(e, '_process_capture', lambda result: (np.zeros(1), np.zeros(1)))
        monkeypatch.setattr(e.verifier, 'calculate_metrics', lambda *args, **kwargs: {
            'psr': 0.25,
            'psr_reference': None,
        })

    result = e.capture_ir(
        session_dir / 'measurement_ir.wav', session_dir / 'Distortion' / 'measurement_dist.wav',
        save=True, include_debug_artifacts=False)

    assert result['ir_linear'] is None
    assert not (session_dir / 'measurement_ir.wav').exists()
    archive_dirs = list((session_dir / 'failed').glob('measurement_*'))
    assert len(archive_dirs) == 1
    capture_path = archive_dirs[0] / 'attempt_01_capture.wav'
    failure_path = archive_dirs[0] / 'attempt_01_failure.json'
    document = json.loads(failure_path.read_text(encoding='utf-8'))
    with sf.SoundFile(capture_path) as wav_info:
        assert wav_info.subtype == 'FLOAT'
    raw, sample_rate = sf.read(capture_path, dtype='float32', always_2d=True)
    assert sample_rate == FS
    assert raw.shape == (len(capture['raw_recordings'][0]), 2)
    np.testing.assert_array_equal(raw[:, 0], capture['raw_recordings'][0])
    np.testing.assert_array_equal(raw[:, 1], capture['raw_recordings'][1])

    assert document['schema_version'] == 1
    assert document['measurement_label'] == 'measurement'
    assert document['attempt'] == document['attempt_limit'] == 1
    assert document['sample_rate'] == FS
    assert document['channel_mapping']['recording_order'] == ['electrical_loopback', 'microphone']
    assert document['channel_mapping']['input_channels'] == {'electrical_loopback': 0, 'microphone': 1}
    assert document['planned_playback_marker_positions_samples'] == capture['capture_metadata']['playback_marker_starts']
    assert document['observed_stream']['actual_sample_rate'] == FS
    assert document['observed_stream']['stream_latency_s'] == 0.012
    assert document['observed_stream']['stream_status'] == status
    assert document['detected_alignment_positions']

    expected_reason = {
        'stream': 'stream health rejection',
        'silence': 'silence gap in loopback',
        'psr': 'quality below threshold (PSR=0.25, threshold=3.00)',
    }[rejection]
    assert expected_reason in document['reason']
    reference_link = document['artifacts']['shared_reference_path_relative_to_failure_json']
    reference_path = (failure_path.parent / reference_link).resolve()
    assert reference_path.is_relative_to((session_dir / 'debug').resolve())
    with np.load(reference_path) as reference:
        for key, values in capture['digital_reference'].items():
            np.testing.assert_array_equal(reference[key], values)
        np.testing.assert_array_equal(reference['marker'], capture['digital_reference']['marker'])


@pytest.mark.parametrize(
    'has_partial_alignment', [False, True],
    ids=['first-marker-failure', 'later-marker-failure'],
)
def test_alignment_rejection_archives_raw_capture_and_partial_positions(
        tmp_path, monkeypatch, has_partial_alignment):
    e = make_engine()
    e.cap['max_capture_retries'] = 1
    session_dir = tmp_path / 'session'
    e.set_session_directory(session_dir)
    prepared = e._prepare_sweep()
    mic, loop = ideal_capture(prepared, acoustic_delay=31)

    def fail_alignment(*args, **kwargs):
        e.alignment_engine.last_alignment = []
        if has_partial_alignment:
            marker_start = prepared['timing']['playback_marker_starts'][0] + 80
            e.alignment_engine.last_alignment.append({
                'repeat': 0,
                'marker_start_sample': marker_start,
                'sweep_start_sample': marker_start + prepared['timing']['sweep_offset_samples'],
            })
        raise audio.CaptureAlignmentError('later marker was not found')

    monkeypatch.setattr(e.alignment_engine, 'sync_and_average', fail_alignment)
    with pytest.raises(audio.CaptureAlignmentError) as failure:
        e._finish_sweep(prepared, mic, loop)
    monkeypatch.setattr(e, '_run_sweep', Mock(side_effect=failure.value))

    result = e.capture_ir(
        session_dir / 'measurement_ir.wav', session_dir / 'Distortion' / 'measurement_dist.wav', save=True)

    assert result['ir_linear'] is None
    archive_dir = next((session_dir / 'failed').glob('measurement_*'))
    capture_path = archive_dir / 'attempt_01_capture.wav'
    failure_path = archive_dir / 'attempt_01_failure.json'
    document = json.loads(failure_path.read_text(encoding='utf-8'))
    raw, _ = sf.read(capture_path, dtype='float32', always_2d=True)
    expected_positions = []
    if has_partial_alignment:
        marker_start = prepared['timing']['playback_marker_starts'][0] + 80
        expected_positions.append({
            'repeat': 1,
            'marker_start_sample': marker_start,
            'sweep_start_sample': marker_start + prepared['timing']['sweep_offset_samples'],
        })
    np.testing.assert_array_equal(raw[:, 0], loop)
    np.testing.assert_array_equal(raw[:, 1], mic)
    assert document['exception'] == {
        'type': 'CaptureAlignmentError', 'message': 'later marker was not found'}
    assert document['planned_playback_marker_positions_samples'] == prepared['timing']['playback_marker_starts']
    assert document['detected_alignment_positions'] == expected_positions
    reference_path = (failure_path.parent /
                      document['artifacts']['shared_reference_path_relative_to_failure_json']).resolve()
    with np.load(reference_path) as reference:
        for key, values in failure.value.capture_context['digital_reference'].items():
            np.testing.assert_array_equal(reference[key], values)


def test_mixed_rejections_archive_each_attempt_and_repeated_measurement_uniquely(
        tmp_path, monkeypatch):
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    session_dir = tmp_path / 'session'
    e.set_session_directory(session_dir)
    underflow = _make_archivable_capture(e, [{'sample': 55, 'status': 'OutputOverflow'}])
    clean_capture = _make_archivable_capture(e)
    captures = iter([underflow, clean_capture, clean_capture, underflow])
    monkeypatch.setattr(e, '_run_sweep', lambda: next(captures))
    silence_results = iter([True, False])
    _replace_loopback_detector(e, lambda context: (
        CaptureIssue('loopback_gaps', 'silence gap in loopback') if next(silence_results) else None))
    monkeypatch.setattr(e, '_process_capture', lambda result: (np.zeros(1), np.zeros(1)))
    monkeypatch.setattr(e.verifier, 'calculate_metrics', lambda *args, **kwargs: {
        'psr': 0.5,
        'psr_reference': None,
    })
    ir_path = session_dir / 'measurement_ir.wav'
    dist_path = session_dir / 'Distortion' / 'measurement_dist.wav'

    first = e.capture_ir(ir_path, dist_path, save=True, include_debug_artifacts=False)
    e.cap['max_capture_retries'] = 1
    second = e.capture_ir(ir_path, dist_path, save=True, include_debug_artifacts=False)

    assert first['ir_linear'] is None and second['ir_linear'] is None
    archive_dirs = list((session_dir / 'failed').glob('measurement_*'))
    assert len(archive_dirs) == 4
    assert len({path.name for path in archive_dirs}) == 4
    documents = [json.loads((path / next(path.glob('*_failure.json')).name).read_text(encoding='utf-8'))
                 for path in archive_dirs]
    reasons = [document['reason'] for document in documents]
    assert any('stream health rejection' in reason for reason in reasons)
    assert any('silence gap in loopback' in reason for reason in reasons)
    assert any('quality below threshold' in reason for reason in reasons)
    assert sorted(document['attempt'] for document in documents) == [1, 1, 2, 3]
    assert not ir_path.exists()
    assert not dist_path.exists()


@pytest.mark.parametrize(('save', 'debug_saves'), [(False, True), (True, False)])
def test_failed_capture_flags_suppress_archives(tmp_path, monkeypatch, save, debug_saves):
    e = make_engine()
    e.cap.update(max_capture_retries=1, debug_saves=debug_saves)
    session_dir = tmp_path / 'session'
    e.set_session_directory(session_dir)
    capture = _make_archivable_capture(e, [{'sample': 10, 'status': 'InputUnderflow'}])
    monkeypatch.setattr(e, '_run_sweep', lambda: capture)

    e.capture_ir(
        session_dir / 'measurement_ir.wav', session_dir / 'Distortion' / 'measurement_dist.wav', save=save)

    assert not (session_dir / 'failed').exists()


def test_failed_archive_write_error_is_logged_and_retry_continues(tmp_path, monkeypatch):
    e = make_engine()
    e.cap['max_capture_retries'] = 2
    session_dir = tmp_path / 'session'
    e.set_session_directory(session_dir)
    capture = _make_archivable_capture(e, [{'sample': 10, 'status': 'InputUnderflow'}])
    calls = []

    def run_sweep():
        calls.append(True)
        return capture

    log = Mock()
    monkeypatch.setattr(e, '_run_sweep', run_sweep)
    monkeypatch.setattr(e, '_save_wav_with_metadata', Mock(side_effect=OSError('disk full')))
    monkeypatch.setattr(audio, 'logger', log)

    result = e.capture_ir(
        session_dir / 'measurement_ir.wav', session_dir / 'Distortion' / 'measurement_dist.wav', save=True)

    assert len(calls) == 2
    assert result['ir_linear'] is None
    assert log.exception.call_count == 2
    assert all('continuing capture retries' in call.args[0] for call in log.exception.call_args_list)
    assert not list((session_dir / 'failed').rglob('*_failure.json'))


def _replace_loopback_detector(engine, check):
    engine.detectors = tuple(replace(detector, check=check)
                             if detector.name == 'loopback_gaps' else detector
                             for detector in engine.detectors)


@pytest.mark.parametrize('operation', ['capture', 'preview'])
@pytest.mark.parametrize('stage', ['raw', 'metrics'])
@pytest.mark.parametrize('persistent', [False, True])
def test_swapped_detector_uses_existing_retry_flow(monkeypatch, tmp_path, operation, stage, persistent):
    engine, capture = setup_capture(monkeypatch, tmp_path)
    engine.cap['max_capture_retries'] = 3
    contexts = []

    def check(context):
        contexts.append(context)
        assert context.capture is capture
        assert context.capture['raw_recordings'][0] is capture['raw_recordings'][0]
        if stage == 'metrics':
            assert context.metrics is not None
            assert context.ir_full is not None and context.ir_linear is not None
        else:
            assert context.metrics is None and context.ir_linear is None
        if persistent or len(contexts) == 1:
            return CaptureIssue('replacement', 'replacement detector rejected capture')
        return None

    engine.detectors = (CaptureDetector(
        'replacement', stage, check, 'retry_check_replacement', 'Replacement', 'Test detector'),)
    if operation == 'capture':
        result = engine.capture_ir(tmp_path / 'ir.wav', tmp_path / 'dist.wav', save=False)
    else:
        result = engine.preview_ir()
    assert len(contexts) == (3 if persistent else 2)
    assert (result['ir_linear'] is None) == persistent
    assert bool(result.get('error')) == persistent


@pytest.mark.parametrize('enabled', [False, True])
def test_factory_loads_all_detector_switches(mock_config, enabled):
    from nfs.capture_detection import DEFAULT_DETECTORS

    config = configparser.ConfigParser()
    config.read(mock_config)
    for detector in DEFAULT_DETECTORS:
        config['sweep'][detector.enabled_key] = str(enabled)
    with mock_config.open('w') as handle:
        config.write(handle)
    engine = AudioFactory.create(str(mock_config))
    assert all(engine.cap[d.enabled_key] is enabled for d in DEFAULT_DETECTORS)
