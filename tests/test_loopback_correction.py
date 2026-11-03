"""Known-system regressions for linear-only electrical reference correction."""
import numpy as np
import pytest
from scipy import signal

from nfs.audio import DeconvolutionEngine, SweepGenerator, HarmonicInjector, ProtectionFilter, SINE_RMS_FROM_PEAK
from nfs.audio import _LINEAR_IR_ORIGIN_SAMPLE as IR_ORIGIN
from test_audio_capture import make_engine, ideal_capture


def spectrum(h, center, fs, seconds=.015):
    half = round(seconds * fs)
    center = round(center)
    z = h[center-half:center+half] * signal.windows.tukey(2*half, .25)
    return np.fft.rfft(z, 32768)


@pytest.mark.parametrize('fs', [44100, 48000, 96000])
def test_linear_correction_preserves_harmonics_and_delay(fs):
    duration = 1.5
    gen = SweepGenerator(fs, duration, 1, -18)
    x, phase, inv = gen.generate()
    mixed = HarmonicInjector(-40, -50).inject(
        x, phase, envelope=gen.fade_envelope, instantaneous_hz=gen.instantaneous_hz, fs=fs)
    # Deliberately much worse reference distortion than the M4. These terms
    # must NOT enter the correction, even when louder than the DUT's H3.
    ref = HarmonicInjector(-30, -35).inject(
        x, phase, envelope=gen.fade_envelope, instantaneous_hz=gen.instantaneous_hz, fs=fs)
    sos = signal.butter(6, .43*fs, fs=fs, output='sos')
    delay = round(.003*fs)
    y = signal.sosfilt(sos, np.pad(mixed, (delay, round(.2*fs)))) * .2
    r = signal.sosfilt(sos, np.pad(ref, (0, delay+round(.2*fs)))) * .07
    engine = DeconvolutionEngine(fs)
    old,_ = engine.process_ir(y,inv, inverse_origin_sample=gen.inverse_origin_sample, sweep_samples=round(gen.T * gen.fs))
    new,linear = engine.process_ir(y,inv,loop_data=r,
                                  reference_data=x,log_sweep_rate=gen.log_sweep_rate, inverse_origin_sample=gen.inverse_origin_sample, sweep_samples=round(gen.T * gen.fs))
    assert abs(np.argmax(abs(linear))-IR_ORIGIN-delay) <= 1
    f = np.fft.rfftfreq(32768, 1/fs)
    band = (f>=1000)&(f<=min(18000,.38*fs))
    # Use windows shorter than the closest H2/H3 spacing, for short sweeps.
    half_s = min(.015, gen.log_sweep_rate*np.log(1.5)*.35)
    center = round(gen.T * gen.fs) - 1+delay
    f_old,f_new = [spectrum(h,center,fs,half_s) for h in (old,new)]
    for order,level in [(2,-40),(3,-50)]:
        at = center-gen.log_sweep_rate*np.log(order)*fs
        a = 20*np.log10(abs(spectrum(old,at,fs,half_s)[band]/f_old[band]))
        b = 20*np.log10(abs(spectrum(new,at,fs,half_s)[band]/f_new[band]))
        assert np.percentile(abs(b-a),99) < .03
        assert np.percentile(abs(b-level),99) < .05
    # Expected gain is retained, even though the reference ADC gain differs.
    ideal,_ = engine.process_ir(np.pad(mixed,(delay,round(.2*fs)))*.2,inv, inverse_origin_sample=gen.inverse_origin_sample, sweep_samples=round(gen.T * gen.fs))
    expected = spectrum(ideal,center,fs,half_s)
    assert np.percentile(abs(20*np.log10(abs(f_new[band]/expected[band]))),99) < .03


@pytest.mark.parametrize('first', [False,True])
def test_marker_indices_and_room_echo_survive_correction(first):
    e=make_engine(first, injected=True)
    e.cap['loopback_correction']=True
    p=e._prepare_sweep()
    mic,loop=ideal_capture(p,delays=(80,80,80),acoustic_delay=23)
    # A real late reflection must not be mistaken for reference filter tail.
    mic[1200:] += .3*mic[:-1200]
    r=e._finish_sweep(p,mic,loop)
    before=[dict(row) for row in r['capture_metadata']['aligned_repeats']]
    e._run_sweep=lambda:r
    from nfs.datatypes import CylindricalPosition
    out=e.measure_ir(CylindricalPosition(100,0,0),save=False)
    assert out['capture_metadata']['aligned_repeats']==before
    ir=out['ir_linear']
    assert np.argmax(abs(ir))==IR_ORIGIN+23
    assert ir[IR_ORIGIN+23+1200]/ir[IR_ORIGIN+23]==pytest.approx(.3,abs=.002)
    assert out['capture_metadata']['loopback_correction']['enabled']


def test_invalid_reference_does_not_silently_claim_correction():
    g=SweepGenerator(48000,.5,1,-18)
    x,_,inv=g.generate()
    e=DeconvolutionEngine(48000)
    with pytest.raises(ValueError):
        e.process_ir(x,inv,loop_data=x, inverse_origin_sample=g.inverse_origin_sample, sweep_samples=round(g.T * g.fs))
    with pytest.raises(ValueError):
        e.process_ir(x,inv,loop_data=np.zeros_like(x),reference_data=x,log_sweep_rate=g.log_sweep_rate, inverse_origin_sample=g.inverse_origin_sample, sweep_samples=round(g.T * g.fs))
    with pytest.raises(ValueError):
        e.process_ir(x,inv,loop_data=x*np.nan,reference_data=x,log_sweep_rate=g.log_sweep_rate, inverse_origin_sample=g.inverse_origin_sample, sweep_samples=round(g.T * g.fs))


def test_late_room_tail_does_not_wrap_and_protection_is_separate():
    fs=48000
    g=SweepGenerator(fs,1.5,1,-18)
    x,_,inv=g.generate()
    tail=round(2.3*fs)
    y=np.pad(x,(0,tail))
    y[round(2*fs):round(2*fs)+len(x)]+=.3*x
    hp=ProtectionFilter(fs,80,1,True,12)
    e=DeconvolutionEngine(fs)
    kwargs=dict(loop_data=np.pad(x,(0,tail)),reference_data=x,log_sweep_rate=g.log_sweep_rate,
                linear_ir_samples=tail+51)
    a,la=e.process_ir(hp.apply(y),inv,hp,**kwargs, inverse_origin_sample=g.inverse_origin_sample, sweep_samples=round(g.T * g.fs))
    b,lb=e.process_ir(hp.apply(y),inv,hp,linear_ir_samples=tail+51, inverse_origin_sample=g.inverse_origin_sample, sweep_samples=round(g.T * g.fs))
    np.testing.assert_allclose(la,lb,atol=2e-6,rtol=0)
    assert np.argmax(abs(la[round(1.8*fs):]))+round(1.8*fs)==IR_ORIGIN+round(2*fs)


def test_corrected_archive_replays_exactly(tmp_path):
    import json
    import soundfile as sf
    from nfs.audio import DSPUtils
    from nfs.datatypes import CylindricalPosition
    e=make_engine(injected=True)
    e.cap['loopback_correction']=True
    p=e._prepare_sweep()
    mic,loop=ideal_capture(p,delays=(80,80,80))
    raw=e._finish_sweep(p,mic,loop)
    e._run_sweep=lambda:raw
    e.set_session_directory(tmp_path)
    out=e.measure_ir(CylindricalPosition(100,0,0),'replay')
    log=next((tmp_path/'debug').glob('*capture_log.json'))
    meta=json.loads(log.read_text())
    data,fs=sf.read(next((tmp_path/'debug').glob('*capture.wav')),dtype='float32')
    refs=np.load(tmp_path/'debug'/meta['reference_file'])
    avg=np.mean([data[r['sweep_start_sample']:r['capture_end_sample']]
                 for r in meta['aligned_repeats']],axis=0)
    full,linear=e.deconv_engine.process_ir(
        DSPUtils.hann_fade(avg[:,1],meta['mic_tail_fade_ms'],fs,side='out'),refs['inverse'],
        linear_ir_samples=meta['linear_ir_samples'],loop_data=avg[:,0],
        loop_valid_samples=meta['loop_valid_samples'],
        reference_data=np.pad(refs['loopback_sweep'],(0,meta['capture_tail_samples'])),
        log_sweep_rate=meta['L_s'], inverse_origin_sample=meta["inverse_origin_sample"], sweep_samples=meta["sweep_samples"])
    np.testing.assert_array_equal(linear,out['ir_linear'])
    np.testing.assert_array_equal(full,out['ir_full'])


@pytest.mark.parametrize('fs', [44100, 48000, 96000])
def test_correction_keeps_room_phase_and_known_ir(fs):
    """Only regularization is minimum phase; the room must retain its phase."""
    gen = SweepGenerator(fs, 1.5, 1, -18)
    x, _, inv = gen.generate()
    x = np.pad(x, (0, round(.6*fs)))
    # A nonminimum-phase interface with unity magnitude isolates phase recovery
    # from the intentional bandwidth changes caused by gain limiting.
    reference = signal.lfilter([.3, 1.], [1., .3], x)
    room = np.zeros(round(.08*fs))
    room[100] = 1
    room[100+round(.008*fs)] = .3
    t = np.arange(round(.004*fs))/fs
    room[100:100+len(t)] += .08*np.sin(2*np.pi*20000*t)*np.exp(-t/.0004)
    room = signal.sosfilt(signal.butter(1, 18, fs=fs, btype='highpass', output='sos'), room)
    mic = signal.fftconvolve(reference, room)
    e = DeconvolutionEngine(fs)
    full, linear = e.process_ir(mic, inv, loop_data=reference, reference_data=x,
                                log_sweep_rate=gen.log_sweep_rate, inverse_origin_sample=gen.inverse_origin_sample, sweep_samples=round(gen.T * gen.fs))
    expected = np.pad(room, (IR_ORIGIN, len(linear)-IR_ORIGIN-len(room))) / np.sqrt(2)
    np.testing.assert_allclose(linear[:round(.2*fs)], expected[:round(.2*fs)], atol=2e-6, rtol=0)
    assert np.argmax(abs(linear)) == IR_ORIGIN + 100


def test_noisy_stopband_does_not_generate_large_inverse_gain():
    fs = 48000
    gen = SweepGenerator(fs, 1.5, 1, -18)
    x, _, inv = gen.generate()
    x = np.pad(x, (0, fs//2))
    reference = signal.sosfilt(signal.butter(8, 18000, fs=fs, output='sos'), x)
    reference += np.random.default_rng(1234).normal(0, 1e-5, len(reference))
    e = DeconvolutionEngine(fs)
    h, linear = e.process_ir(reference, inv, loop_data=reference,
                             reference_data=x, log_sweep_rate=gen.log_sweep_rate, inverse_origin_sample=gen.inverse_origin_sample, sweep_samples=round(gen.T * gen.fs))
    assert np.all(np.isfinite(h))
    assert e.last_loopback_correction['finite_kernel_max_boost_db'] <= e.last_loopback_correction['max_boost_db'] + .1
    assert np.linalg.norm(linear) < 1


@pytest.mark.parametrize('fs', [44100, 48000, 96000])
def test_no_output_highpass_with_or_without_correction(fs):
    g = SweepGenerator(fs, 1.5, 1, -6)
    x, _, inv = g.generate()
    kw = dict(inverse_origin_sample=g.inverse_origin_sample, sweep_samples=len(x))
    e = DeconvolutionEngine(fs)
    _, direct = e.process_ir(x, inv, **kw)
    _, corrected = e.process_ir(x, inv, loop_data=x, reference_data=x,
                               log_sweep_rate=g.log_sweep_rate, **kw)
    truth = np.zeros_like(direct); truth[IR_ORIGIN] = SINE_RMS_FROM_PEAK
    np.testing.assert_allclose(direct, truth, atol=2e-6, rtol=0)
    np.testing.assert_allclose(corrected, direct, atol=2e-6, rtol=0)



def test_clean_reference_boundary_excludes_following_marker():
    fs = 48000; g = SweepGenerator(fs, 1.5, 1, -6)
    x, _, inv = g.generate(); n = len(x)
    ref = np.pad(x, (0, fs//2))
    contaminated = ref.copy(); contaminated[-50:] = .5
    kw = dict(inverse_origin_sample=g.inverse_origin_sample, sweep_samples=n,
              log_sweep_rate=g.log_sweep_rate, loop_valid_samples=len(ref)-100)
    e = DeconvolutionEngine(fs)
    a,_ = e.process_ir(ref,inv,loop_data=ref,reference_data=ref,**kw)
    b,_ = e.process_ir(ref,inv,loop_data=contaminated,reference_data=ref,**kw)
    np.testing.assert_array_equal(a,b)

