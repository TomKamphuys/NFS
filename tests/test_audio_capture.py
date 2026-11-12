"""Capture/DSP regressions using synthetic signals; no real hardware is opened."""

import configparser
import json
import shutil
from unittest.mock import Mock

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

import nfs.audio as audio
from nfs.audio import AlignmentEngine, MarkerGenerator, SweepGenerator, DeconvolutionEngine, HarmonicInjector, DSPUtils
from nfs.audio import AudioFactory, SINE_RMS_FROM_PEAK
from nfs.datatypes import CylindricalPosition
from nfs.nfs import NearFieldScanner

FS = 48000


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
        retry_psr_threshold=3.0,
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

def test_check_stream_health_detects_underflow():
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
    assert e._check_stream_health(result) is True


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
    assert e._check_stream_health(result) is True


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
    assert e._check_stream_health(result) is False


def test_check_stream_health_clean_when_empty_status():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    result = {
        'capture_metadata': {},
        'raw_recordings': (np.ones(1000), np.ones(1000)),
    }
    assert e._check_stream_health(result) is False


def test_check_silence_gaps_detects_gap_in_loopback():
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
    assert e._check_silence_gaps(result) is True


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
    assert e._check_silence_gaps(result) is False


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
    assert e._check_silence_gaps(result) is False


def test_check_quality_flags_low_psr():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    metrics = {'psr': 1.5, 'snr_db': 60.0}
    assert e._check_quality(metrics) is True


def test_check_quality_passes_high_psr():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    metrics = {'psr': 5.0, 'snr_db': 60.0}
    assert e._check_quality(metrics) is False


def test_check_quality_ignores_low_snr():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 3.0
    # Low SNR should NOT trigger retry — only PSR matters
    metrics = {'psr': 5.0, 'snr_db': 10.0}
    assert e._check_quality(metrics) is False


def test_check_quality_uses_configurable_threshold():
    e = make_engine()
    e.cap['max_capture_retries'] = 3
    e.cap['retry_psr_threshold'] = 5.0
    metrics = {'psr': 4.0, 'snr_db': 60.0}
    assert e._check_quality(metrics) is True
    metrics = {'psr': 6.0, 'snr_db': 60.0}
    assert e._check_quality(metrics) is False


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
    assert 'dropout' in exported['error'].lower()

