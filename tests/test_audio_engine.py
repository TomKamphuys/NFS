import pytest
import numpy as np
import scipy.signal
import configparser
import json
import os
from pathlib import Path
from nfs.audio import (
    MarkerGenerator, SweepGenerator, HarmonicInjector,
    ProtectionFilter, DeconvolutionEngine, AlignmentEngine,
    DSPVerificationTool, AudioFactory
)
from nfs.utils.dsp import DSPUtils
from nfs.datatypes import CylindricalPosition


@pytest.fixture
def fs():
    return 48000


# -----------------------------------------------------------------------------
# SIGNAL GENERATORS
# -----------------------------------------------------------------------------

def test_marker_generator_functional(fs):
    dur_ms = 100.0
    bw_hz = (500.0, 5000.0)
    level_dbfs = -6.0

    gen = MarkerGenerator(fs, dur_ms, bw_hz, level_dbfs)
    marker = gen.generate()

    # Check duration
    expected_len = int(round(dur_ms / 1000.0 * fs))
    assert len(marker) == expected_len

    # Check level
    peak = np.max(np.abs(marker))
    assert np.isclose(20 * np.log10(peak), level_dbfs, atol=0.1)

    # Check bandwidth (simple check: spectrum should be low outside bw)
    Nfft = 4096
    freqs = np.fft.rfftfreq(Nfft, 1 / fs)
    spec = np.abs(np.fft.rfft(marker, n=Nfft))
    spec /= (np.max(spec) + 1e-12)

    # Look at 100Hz (well below 500Hz)
    idx_100 = np.searchsorted(freqs, 100)
    assert spec[idx_100] < 0.15

    # Look at 10000Hz (well above 5000Hz)
    idx_10k = np.searchsorted(freqs, 10000)
    assert spec[idx_10k] < 0.15


def test_sweep_generator_functional(fs):
    dur_s = 0.5
    f1 = 20.0
    level_dbfs = -10.0

    gen = SweepGenerator(fs, dur_s, f1, level_dbfs)
    s_fund, phase, inv = gen.generate()

    # Check lengths
    expected_n = int(round(dur_s * fs))
    assert len(s_fund) == expected_n
    assert len(inv) == expected_n

    # Check inverse filter property: s_fund * inv should yield a delta-like pulse in frequency domain (unity gain)
    Nfft = 2 ** int(np.ceil(np.log2(len(s_fund) + len(inv) - 1)))
    S = np.fft.rfft(s_fund, n=Nfft)
    I = np.fft.rfft(inv, n=Nfft)
    H = S * I

    # The magnitude should be flat across the sweep range
    mag_h = np.abs(H)
    f = np.fft.rfftfreq(Nfft, 1 / fs)
    mask = (f > f1 * 2) & (f < fs * 0.45)  # Avoid edges

    assert np.all(np.isclose(mag_h[mask], 1.0, atol=0.5))
    assert np.mean(mag_h[mask]) == pytest.approx(1.0, abs=0.1)


# -----------------------------------------------------------------------------
# PIPELINE STEPS
# -----------------------------------------------------------------------------

def test_harmonic_injector_functional(fs):
    dur_s = 1.0
    f1 = 100.0
    level_dbfs = 0.0

    gen = SweepGenerator(fs, dur_s, f1, level_dbfs)
    s_fund, phase, _ = gen.generate()

    # Inject H2 at -20dB and H3 at -40dB
    h2_db = -20.0
    h3_db = -40.0
    injector = HarmonicInjector(h2_db=h2_db, h3_db=h3_db)
    s_comp = injector.inject(s_fund, phase)

    # Verify it's different from fund and matches expected injection
    diff = s_comp - s_fund
    expected_diff_h2 = DSPUtils.db_to_lin(h2_db) * np.sin(2 * phase)
    expected_diff_h3 = DSPUtils.db_to_lin(h3_db) * np.sin(3 * phase)
    assert np.allclose(diff, expected_diff_h2 + expected_diff_h3, atol=1e-5)


def test_protection_filter_functional(fs):
    fc = 1000.0
    order = 4

    # Test magnitude response of apply()
    p_filter = ProtectionFilter(fs, fc, order)

    # White noise test
    noise = np.random.normal(0, 0.1, fs)
    filtered = p_filter.apply(noise)

    # Check frequency response
    f, pxx_den = scipy.signal.welch(filtered, fs, nperseg=1024)
    idx_500 = np.searchsorted(f, 500)
    idx_2000 = np.searchsorted(f, 2000)
    assert pxx_den[idx_500] < pxx_den[idx_2000] * 0.01  # 20dB difference at least

    # Test correction mask
    p_filter_corr = ProtectionFilter(fs, fc, order, hpf_correction=True, hpf_corr_db_cap=6.0)
    n_bins = 1024
    mask = p_filter_corr.get_correction_mask(n_bins)

    assert np.max(np.abs(mask)) <= 2.0 + 1e-6  # Capped at +6dB
    assert mask[0] == 0j  # DC kill


# -----------------------------------------------------------------------------
# ENGINES
# -----------------------------------------------------------------------------

def test_alignment_engine_functional(fs):
    num_sweeps = 1
    align_to_first = True
    taper_ms = 10.0
    marker_dur_ms = 100.0

    engine = AlignmentEngine(fs, num_sweeps, align_to_first, taper_ms, marker_dur_ms)

    # Create marker
    gen = MarkerGenerator(fs, marker_dur_ms, (500.0, 5000.0), -6.0)
    marker = gen.generate()

    # Create simulated recordings with delay
    delay_samps = 1000
    rec_loop = np.zeros(fs)
    rec_loop[delay_samps:delay_samps + len(marker)] = marker

    mic_delay = 500
    rec_mic = np.zeros(fs)
    rec_mic[delay_samps + mic_delay: delay_samps + mic_delay + len(marker)] = marker

    avg_mic, avg_loop, slices, psr = engine.sync_and_average(
        rec_mic, rec_loop, marker, 0, fs // 2, fs // 4
    )

    assert psr > 3.0
    assert len(avg_mic) == fs // 4 + int(round(taper_ms / 1000.0 * fs))


def test_deconvolution_separation_functional(fs):
    dur_s = 0.5
    f1 = 20.0
    level_dbfs = -10.0

    gen = SweepGenerator(fs, dur_s, f1, level_dbfs)
    s_fund, phase, inv = gen.generate()

    # Inject heavy H2 (-10dB relative to fund)
    injector = HarmonicInjector(h2_db=-10.0)
    s_distorted = injector.inject(s_fund, phase)

    engine = DeconvolutionEngine(fs)
    h_full, h_linear = engine.process_ir(s_distorted, inv)

    # In Farina's method, H2 appears at negative time: delta_t = L * ln(2)
    L = dur_s / np.log((fs / 2) / f1)
    dt_h2 = L * np.log(2)
    offset_samps = int(round(dt_h2 * fs))
    split_idx = len(inv) - 5
    h2_peak_idx = split_idx - offset_samps

    # Verify energy at h2_peak_idx
    window = h_full[max(0, h2_peak_idx - 100): h2_peak_idx + 100]
    assert np.max(np.abs(window)) > 0.1

    # Verify linear peak
    assert np.max(np.abs(h_linear)) > 0.5


# -----------------------------------------------------------------------------
# VERIFICATION & METRICS
# -----------------------------------------------------------------------------

def test_snr_calculation(fs):
    verifier = DSPVerificationTool(fs)
    h_linear = np.zeros(fs)
    h_linear[100] = 1.0
    h_full = h_linear.copy()
    noise_floor = 0.001  # -60dB
    h_full[-5000:] = np.random.normal(0, noise_floor, 5000)

    metrics = verifier.calculate_metrics(h_full, h_linear, 10.0)
    assert 55 < metrics['snr_db'] < 65


def test_thd_calculation(fs):
    verifier = DSPVerificationTool(fs)
    h_linear = np.zeros(fs)
    h_linear[100] = 1.0
    h_full = np.zeros(fs * 2)
    h_full[fs:fs + fs] = h_linear
    # Put distortion in negative time
    h_full[1000:1100] = 0.1

    metrics = verifier.calculate_metrics(h_full, h_linear, 10.0)
    assert metrics['thd_pct'] > 50


def test_verification_warnings(fs):
    verifier = DSPVerificationTool(fs)
    assert len(verifier.verify({'snr_db': 50, 'thd_pct': 0.1, 'psr': 10.0})) == 0
    assert any("LOW SNR" in w for w in verifier.verify({'snr_db': 20, 'thd_pct': 0.1, 'psr': 10.0}))
    assert any("HIGH DISTORTION" in w for w in verifier.verify({'thd_pct': 15.0}))
    assert any("POOR ALIGNMENT" in w for w in verifier.verify({'psr': 2.0}))


# -----------------------------------------------------------------------------
# INTEGRATION / MOCK
# -----------------------------------------------------------------------------

def test_integration_with_mock_audio(tmp_path):
    config = configparser.ConfigParser()
    config['audio'] = {
        'mode': 'mock_interface', 'fs': '48000', 'in_dev': '0', 'out_dev': '0',
        'in_ch_mic': '1', 'in_ch_loop': '0', 'out_ch_spkr': '0', 'out_ch_ref': '1',
        'blocksize': '1024', 'wasapi_exclusive': 'False'
    }
    config['sweep'] = {
        'sweep_dur_s': '0.5', 'sweep_level_dbfs': '-10', 'num_sweeps': '1',
        'pre_sil_ms': '50', 'post_sil_ms': '50', 'mic_tail_taper_ms': '10',
        'align_to_first_marker': 'True', 'debug_saves': 'True',
        'H2_TEST_DB': 'None', 'H3_TEST_DB': 'None',
        'protect_hpf_hz': '0',
        'protect_hpf_order': '4',
        'protect_hpf_correction': 'False',
        'protect_hpf_corr_db_cap': '12.0',
    }

    config_path = tmp_path / "dsp_test_config.ini"
    with open(config_path, "w") as f:
        config.write(f)

    audio = AudioFactory.create(str(config_path))
    audio.set_session_directory(tmp_path)
    pos = CylindricalPosition(100, 0, 10)
    audio.measure_ir(pos, "DSP_TEST")

    # Check if metrics file was created
    # Based on log output: Saved Linear IR: DSP_TEST_r100_ph0_z10_ir.wav
    metrics_file = tmp_path / "Recordings" / "debug" / "DSP_TEST_r100_ph0_z10_metrics.json"
    assert metrics_file.exists()
