"""Golden-reference controls ported from the standalone proof of concept.

The signal simulation and injected cases are deliberately independent of the
production capture adapter. No hardware is opened.
"""

from typing import Callable
from dataclasses import replace
import json

import numpy as np
import pytest
from scipy.signal import butter, sosfilt

from nfs.capture_detection.loopback_reference import (
    Capture, Layout, Limits, Reference, ERROR_FLAGS, align_capture, shifted_segment,
    establish_reference, check_capture, compare_waveforms, residual_windows, residual_thresholds,
)

def simulated_source(seed: int, repeats: int = 2) -> tuple[Layout, np.ndarray, Callable[[], Capture]]:
    fs, duration = 96000, 1.5
    guard = round(.01 * fs)
    active = round(duration * fs) - 2 * guard
    rate = (active / fs) / np.log(fs / 2)
    time = np.arange(active) / fs
    envelope = np.ones(active)
    fade = round(.001 * fs)
    envelope[:fade] = np.sin(np.linspace(0, np.pi / 2, fade)) ** 2
    envelope[-fade:] = envelope[:fade][::-1]
    sweep = np.pad(np.sin(2 * np.pi * rate * np.expm1(time / rate)) * envelope * .15,
                   (guard, guard))
    chips = np.array([1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1])
    marker = np.interp(np.linspace(0, 12, round(.1 * fs)), np.arange(13), chips)
    marker = sosfilt(butter(3, [500, 5000], fs=fs, btype='bandpass', output='sos'), marker)
    marker *= .15 / np.max(abs(marker))
    offset, post, pre = len(marker) + round(.02 * fs), round(.5 * fs), round(.5 * fs)
    slot = offset + len(sweep) + post
    starts = [pre + repeat * slot for repeat in range(repeats)]
    layout = Layout(fs, pre + repeats * slot + post, starts, len(marker), offset,
                    len(sweep), post, guard, rate)
    tx = np.zeros(layout.total_samples)
    for start in starts:
        tx[start:start + len(marker)] = marker
        tx[start + offset:start + offset + len(sweep)] = sweep
    # Repeatable LF/HF interface roll-off is deliberately part of the reference.
    analogue = sosfilt(butter(1, 3., fs=fs, btype='highpass', output='sos'), tx)
    analogue = sosfilt(butter(3, 25000., fs=fs, output='sos'), analogue)
    rng = np.random.default_rng(seed)

    def acquire() -> Capture:
        delay = 4000 + rng.uniform(-.7, .7)
        padded = np.pad(analogue, (5000, 5000))
        received = shifted_segment(padded, 5000 - delay, len(tx))
        return Capture(received * (1 + rng.normal(0, .0002)) + rng.normal(0, 2e-6, len(tx)))

    return layout, marker, acquire


def fault_cases(base: Capture, reference: Reference, marker: np.ndarray,
                seed: int) -> list[tuple[str, Capture, bool]]:
    layout = reference.layout
    _, positions, _ = align_capture(base, layout, marker, reference.limits, reference.marker_anchor)
    fs = layout.fs
    peak = float(np.max(abs(base.loopback)))
    rng = np.random.default_rng(seed)
    cases = []

    def add(name: str, repeat: int, offset: float, duration: float,
            operation: Callable[[np.ndarray], np.ndarray]) -> None:
        data = base.loopback.copy()
        start = round(positions[repeat] + offset * fs)
        count = max(1, round(duration * fs))
        data[start:start + count] = operation(data[start:start + count])
        cases.append((name, Capture(data, list(base.driver_events)), False))

    sweep_start = layout.sweep_offset / fs + layout.guard_samples / fs
    active_duration = (layout.sweep_samples - 2 * layout.guard_samples) / fs
    middle = sweep_start + active_duration * .5
    loss_duration = min(.02, active_duration * .05)
    for repeat in range(len(positions)):
        for label, offset in [('early_LF', min(.05, active_duration * .1)),
                              ('middle', active_duration * .5),
                              ('late_HF', active_duration * .9)]:
            add(f'repeat{repeat + 1}_{label}_loss', repeat,
                sweep_start + offset, loss_duration, np.zeros_like)
        add(f'repeat{repeat + 1}_marker_interruption', repeat, .035, .02, np.zeros_like)
        add(f'repeat{repeat + 1}_marker_missing', repeat, 0., layout.marker_samples / fs, np.zeros_like)
    add('middle_5ms_loss', 0, middle, min(.005, loss_duration), np.zeros_like)
    add('middle_90percent_attenuation', 0, middle, loss_duration, lambda x: x * .1)
    add('single_sample_click_at_peak_amplitude', 0, middle, 1 / fs, lambda x: x + peak)
    add('early_LF_noise_burst', 0, sweep_start + min(.05, active_duration * .1), loss_duration,
        lambda x: x + rng.normal(0, peak * .2, len(x)))
    add('marker_noise_burst', 0, .035, .01, lambda x: x + rng.normal(0, peak, len(x)))
    # A timing jump inside one sweep must survive marker alignment.
    add('middle_repeated_128_samples', 0, middle, min(.2, active_duration * .25),
        lambda x: np.r_[np.repeat(x[0], 128), x[:-128]])
    cases.append(('ten_percent_gain_change', Capture(base.loopback * 1.1), False))
    cases.append(('small_gain_variation', Capture(base.loopback * 1.001), True))
    padded = np.pad(base.loopback, (256, 256))
    cases.append(('fractional_transport_delay', Capture(
        shifted_segment(padded, 254.75, len(base.loopback))), True))
    cases.append(('driver_flags_all_false', Capture(base.loopback.copy(),
                  [dict.fromkeys(ERROR_FLAGS, False)]), True))
    cases.append(('silent_recording', Capture(np.zeros_like(base.loopback)), False))
    cases.append(('truncated_recording', Capture(base.loopback[:-100]), False))
    invalid = base.loopback.copy()
    invalid[len(invalid) // 2] = np.nan
    cases.append(('nonfinite_recording', Capture(invalid), False))
    for flag in ERROR_FLAGS:
        cases.append((flag, Capture(base.loopback.copy(), [{flag: True}]), False))
    return cases



@pytest.fixture(scope='module')
def simulated_reference():
    layout, marker, acquire = simulated_source(2026, repeats=1)
    bootstrap = [acquire() for _ in range(3)]
    reference, pairs = establish_reference(bootstrap, layout, marker, Limits())
    return reference, marker, bootstrap, acquire


def test_median_reference_and_independent_holdouts(simulated_reference):
    reference, marker, bootstrap, acquire = simulated_reference
    rows = [align_capture(c, reference.layout, marker, reference.limits,
                          reference.marker_anchor)[0] for c in bootstrap]
    np.testing.assert_allclose(reference.waveform, np.median(rows, axis=0), atol=1e-12)
    for _ in range(10):
        result = check_capture(acquire(), reference, marker)
        assert result['accepted'], result


def test_prototype_faults_and_positive_controls(simulated_reference):
    reference, marker, bootstrap, _ = simulated_reference
    for name, capture, expected in fault_cases(bootstrap[0], reference, marker, 123):
        result = check_capture(capture, reference, marker)
        assert result['accepted'] is expected, (name, result)


@pytest.mark.parametrize('position', range(3))
@pytest.mark.parametrize('fault', ['sweep', 'marker', 'driver'])
def test_bootstrap_rejects_a_bad_member(simulated_reference, position, fault):
    reference, marker, bootstrap, _ = simulated_reference
    cases = dict((name, capture) for name, capture, _ in
                 fault_cases(bootstrap[position], reference, marker, 456))
    bad = {'sweep': 'repeat1_middle_loss', 'marker': 'repeat1_marker_missing',
           'driver': 'input_overflow'}[fault]
    captures = list(bootstrap)
    captures[position] = cases[bad]
    with pytest.raises(ValueError):
        establish_reference(captures, reference.layout, marker, reference.limits)


@pytest.mark.parametrize('count', [1, 2, 4])
def test_single_reference_matches_every_measurement_repeat(simulated_reference, count):
    reference, marker, _, _ = simulated_reference
    layout, _, acquire = simulated_source(3010 + count, repeats=count)
    assert reference.waveform.shape[0] == 1
    result = check_capture(acquire(), reference, marker, layout)
    assert result['accepted'], result
    assert len(result['marker_positions']) == count


def test_legacy_reference_archive_keeps_its_original_layout(simulated_reference):
    from dataclasses import replace
    from nfs.capture_detection.loopback_reference import GoldenLoopback, archive_loopback_reference, load_loopback_reference

    reference, marker, _, _ = simulated_reference
    legacy = replace(reference, waveform=np.repeat(reference.waveform, 2, axis=0),
                     layout=reference.layout.with_repeats(2), method_version=1,
                     limits=Limits(threshold_method='local_rms_v1'))
    restored = load_loopback_reference(archive_loopback_reference(GoldenLoopback(legacy, marker, [])))
    assert restored.reference.method_version == 1
    assert restored.reference.waveform.shape[0] == 2
    assert restored.reference.layout == legacy.layout


def test_frequency_rule_is_independent_of_local_signal_level(simulated_reference):
    reference, _, _, _ = simulated_reference
    values = residual_windows(reference.waveform[0], reference.waveform[0], reference.layout, Limits())
    original = residual_thresholds(values, reference.layout, Limits())
    # Zero crossings and crests must not change the new allowance's denominator.
    for local in (np.zeros_like(values['local_reference_rms']),
                  np.full_like(values['local_reference_rms'], 1000.)):
        np.testing.assert_array_equal(original, residual_thresholds(
            dict(values, local_reference_rms=local), reference.layout, Limits()))


@pytest.mark.parametrize('fs', [48000, 96000])
def test_frequency_allowance_has_one_percent_floor_and_smoothly_joins_hf(simulated_reference, fs):
    reference, _, _, _ = simulated_reference
    old = reference.layout
    scale = fs / old.fs
    layout = replace(old, fs=fs, total_samples=round(old.total_samples*scale),
        marker_starts=[round(v*scale) for v in old.marker_starts],
        marker_samples=round(old.marker_samples*scale), sweep_offset=round(old.sweep_offset*scale),
        sweep_samples=round(old.sweep_samples*scale), tail_samples=round(old.tail_samples*scale),
        guard_samples=round(old.guard_samples*scale))
    # Keep the same physical 10 kHz centre at either sample rate.
    centre = (layout.sweep_offset + layout.guard_samples)/fs + layout.log_sweep_rate*np.log(10000)
    values = dict(starts=np.array([0., centre*fs-.005*fs]), reference_rms=1., sweep_reference_rms=1.,
                  local_reference_rms=np.array([0., 0.]))
    thresholds = residual_thresholds(values, layout, Limits())
    assert thresholds[0] == pytest.approx(.01, rel=1e-9)
    old_db = 20*np.log10(.013675918570)
    expected_db = old_db + (4-abs(old_db+40))**2/16
    assert 20*np.log10(thresholds[1]) == pytest.approx(expected_db, abs=1e-8)


def test_sweep_end_guards_keep_hf_allowance_but_quiet_tail_does_not(simulated_reference):
    reference, _, _, _ = simulated_reference
    layout = reference.layout
    end = layout.sweep_offset + layout.sweep_samples
    values = dict(starts=np.array([end-layout.guard_samples, end-1, end]),
                  reference_rms=1., sweep_reference_rms=1., local_reference_rms=np.zeros(3))
    thresholds = residual_thresholds(values, layout, Limits())
    assert thresholds[0] == pytest.approx(thresholds[1])
    assert thresholds[0] > .06  # 48 kHz end frequency at this 96 kHz rate.
    assert thresholds[2] == pytest.approx(.01, rel=1e-9)


def test_same_residual_passes_at_hf_but_rejects_in_quiet_tail(simulated_reference):
    reference, _, _, _ = simulated_reference
    layout = reference.layout
    expected = reference.waveform
    overall = np.sqrt(np.mean(expected**2))
    count = round(.02*layout.fs)
    # Two percent of whole-reference RMS: above quiet allowance, below HF allowance.
    disturbance = np.sin(2*np.pi*1000*np.arange(count)/layout.fs)*np.sqrt(2)*.02*overall
    centre = (layout.sweep_offset+layout.guard_samples)/layout.fs + layout.log_sweep_rate*np.log(20000)
    high = expected.copy()
    start = round((centre-.01)*layout.fs)
    high[0, start:start+count] += disturbance
    assert compare_waveforms(high, expected, layout, Limits())['accepted']
    quiet = expected.copy()
    start = layout.sweep_offset+layout.sweep_samples+round(.1*layout.fs)
    quiet[0, start:start+count] += disturbance
    assert not compare_waveforms(quiet, expected, layout, Limits())['accepted']


@pytest.mark.parametrize('version', [1, 2])
def test_older_archive_replays_original_threshold_instead_of_new_default(simulated_reference, version):
    from nfs.capture_detection.loopback_reference import GoldenLoopback, archive_loopback_reference, load_loopback_reference
    reference, marker, _, _ = simulated_reference
    fields = archive_loopback_reference(GoldenLoopback(replace(reference, method_version=version), marker, []))
    metadata = json.loads(fields['loopback_reference_metadata_json'].item())
    metadata['limits'] = {k: v for k, v in metadata['limits'].items() if k in (
        'relative_rms', 'overall_rms_floor', 'window_ms', 'step_ms', 'minimum_marker_correlation')}
    fields['loopback_reference_metadata_json'] = np.array(json.dumps(metadata))
    legacy = load_loopback_reference(fields).reference
    assert legacy.limits.threshold_method == 'local_rms_v1'
    actual = legacy.waveform.copy()
    layout = legacy.layout
    start = layout.sweep_offset+layout.sweep_samples+round(.1*layout.fs)
    actual[0, start:start+round(.04*layout.fs)] += .0055*np.sqrt(np.mean(actual**2))
    assert not compare_waveforms(actual, legacy.waveform, layout, legacy.limits)['accepted']
    assert compare_waveforms(actual, legacy.waveform, layout, Limits())['accepted']


def test_v5_archive_preserves_sweep_normalization_frequency_rule_and_margin(simulated_reference):
    from nfs.capture_detection.loopback_reference import GoldenLoopback, archive_loopback_reference, load_loopback_reference
    reference, marker, _, _ = simulated_reference
    fields = archive_loopback_reference(GoldenLoopback(reference, marker, []))
    metadata = json.loads(fields['loopback_reference_metadata_json'].item())
    assert metadata['schema'] == 'loopback_reference_v5'
    assert metadata['limits']['threshold_method'] == 'frequency_rms_v2'
    assert metadata['limits']['minimum_rms_ratio'] == .01
    assert metadata['limits']['frequency_margin_db'] == 6.
    assert metadata['limits']['rms_normalization'] == 'active_sweep'
    assert load_loopback_reference(fields).reference.limits == reference.limits


def test_tail_length_does_not_change_sweep_normalized_allowance(simulated_reference):
    reference, _, _, _ = simulated_reference
    layout = reference.layout
    waveform = reference.waveform[0]
    original = residual_windows(waveform, waveform, layout, Limits())
    extra = layout.fs*2
    longer = replace(layout, total_samples=layout.total_samples+extra, tail_samples=layout.tail_samples+extra)
    padded = np.pad(waveform, (0, extra))
    extended = residual_windows(padded, padded, longer, Limits())
    assert extended['reference_rms'] < original['reference_rms']
    assert extended['sweep_reference_rms'] == original['sweep_reference_rms']
    # Match all common windows except the old end-anchored final one.
    old_threshold = residual_thresholds(original, layout, Limits())
    new_threshold = residual_thresholds(extended, longer, Limits())
    locations = np.searchsorted(extended['starts'], original['starts'][:-1])
    np.testing.assert_array_equal(original['starts'][:-1], extended['starts'][locations])
    np.testing.assert_allclose(old_threshold[:-1], new_threshold[locations], rtol=1e-14)


def test_marker_and_tail_energy_do_not_set_sweep_normalizer(simulated_reference):
    reference, _, _, _ = simulated_reference
    layout = reference.layout
    waveform = reference.waveform[0].copy()
    original = residual_windows(waveform, waveform, layout, Limits())
    active_start = layout.sweep_offset+layout.guard_samples
    active_end = layout.sweep_offset+layout.sweep_samples-layout.guard_samples
    waveform[:active_start] += 3.
    waveform[active_end:] -= 2.
    changed = residual_windows(waveform, waveform, layout, Limits())
    assert changed['reference_rms'] > original['reference_rms']
    assert changed['sweep_reference_rms'] == original['sweep_reference_rms']
    np.testing.assert_array_equal(residual_thresholds(original, layout, Limits()),
                                  residual_thresholds(changed, layout, Limits()))


def test_v3_replay_keeps_whole_reference_rms_and_original_curve(simulated_reference):
    from nfs.capture_detection.loopback_reference import GoldenLoopback, archive_loopback_reference, load_loopback_reference
    reference, marker, _, _ = simulated_reference
    old_limits = Limits(threshold_method='frequency_rms_v1', rms_normalization='whole_reference', frequency_rms_floor=.003, frequency_rms_per_hz=7e-7)
    fields = archive_loopback_reference(GoldenLoopback(replace(reference, method_version=3, limits=old_limits), marker, []))
    metadata = json.loads(fields['loopback_reference_metadata_json'].item())
    del metadata['limits']['rms_normalization']  # Genuine pre-v4 metadata.
    fields['loopback_reference_metadata_json'] = np.array(json.dumps(metadata))
    restored = load_loopback_reference(fields).reference
    assert restored.limits == old_limits
    actual = restored.waveform.copy()
    layout = restored.layout
    start = layout.sweep_offset+layout.sweep_samples+round(.1*layout.fs)
    actual[0,start:start+round(.04*layout.fs)] += .0062*np.sqrt(np.mean(actual**2))
    assert not compare_waveforms(actual, restored.waveform, layout, restored.limits)['accepted']
    assert compare_waveforms(actual, restored.waveform, layout, Limits())['accepted']


def test_single_reference_still_validates_full_recording_length(simulated_reference):
    reference, marker, _, _ = simulated_reference
    layout, _, acquire = simulated_source(3009, repeats=4)
    capture = acquire()
    capture.loopback = capture.loopback[:layout.total_samples - 100]
    result = check_capture(capture, reference, marker, layout)
    assert result['accepted'] is False
    assert 'length' in result['reason']


def test_smooth_floor_preserves_hf_and_has_continuous_slope():
    from nfs.capture_detection.loopback_reference import frequency_residual_ratio
    limits = Limits()
    frequency = np.geomspace(1., 100000., 10000)
    old = frequency_residual_ratio(frequency, replace(limits, threshold_method='frequency_rms_v1'))
    new = frequency_residual_ratio(frequency, limits)
    assert new[0] == .01
    assert np.all(new >= old)
    assert np.all(new >= .01)
    assert np.all(np.diff(new) >= 0)
    np.testing.assert_array_equal(new[frequency >= 15000], old[frequency >= 15000])
    # Check slopes where the compact smooth maximum joins its two branches.
    for ratio in (10**(-44/20), 10**(-36/20)):
        f = np.sqrt((ratio/10**(6/20))**2-.0027**2)/6.3e-7
        h = .001
        y = frequency_residual_ratio(np.array([f-h, f, f+h]), limits)
        assert (y[1]-y[0])/h == pytest.approx((y[2]-y[1])/h, abs=1e-11)


def test_v4_archive_keeps_original_floor(simulated_reference):
    from nfs.capture_detection.loopback_reference import GoldenLoopback, archive_loopback_reference, load_loopback_reference
    reference, marker, _, _ = simulated_reference
    old = replace(reference, method_version=4, limits=Limits(threshold_method='frequency_rms_v1'))
    fields = archive_loopback_reference(GoldenLoopback(old, marker, []))
    metadata = json.loads(fields['loopback_reference_metadata_json'].item())
    del metadata['limits']['minimum_rms_ratio']
    fields['loopback_reference_metadata_json'] = np.array(json.dumps(metadata))
    restored = load_loopback_reference(fields).reference
    values = dict(starts=np.array([0]), reference_rms=1., sweep_reference_rms=1.)
    assert residual_thresholds(values, restored.layout, restored.limits)[0] == pytest.approx(.005387208250)
