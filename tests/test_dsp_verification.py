import pytest
import numpy as np
import os
import shutil
from pathlib import Path
from nfs.audio import DSPVerificationTool, AlignmentEngine, DeconvolutionEngine


def test_snr_calculation():
    fs = 48000
    verifier = DSPVerificationTool(fs)

    # Create a synthetic IR: a single spike (Dirac delta)
    h_linear = np.zeros(fs)
    h_linear[100] = 1.0

    # h_full with some noise at the end
    h_full = h_linear.copy()
    noise_floor = 0.001  # -60dB
    h_full[-5000:] = np.random.normal(0, noise_floor, 5000)

    metrics = verifier.calculate_metrics(h_full, h_linear, 10.0)

    # SNR should be approx 20 * log10(1.0 / noise_floor) = 60dB
    # Using std dev of noise, it might be slightly different
    assert 55 < metrics['snr_db'] < 65


def test_snr_calculation_with_raw_mic_signal():
    """Test SNR calculation using raw mic signal pre-silence region (the fix for the bug)."""
    fs = 48000
    verifier = DSPVerificationTool(fs)

    # Create a synthetic IR: a single spike (Dirac delta)
    h_linear = np.zeros(fs)
    h_linear[100] = 1.0

    # h_full - no noise at the end (simulating the deconvolution suppression problem)
    h_full = h_linear.copy()

    # Raw mic signal: pre-silence region has real mic noise, rest has signal
    raw_mic = np.zeros(fs)
    noise_floor = 0.001  # -60dB relative to peak=1.0
    # First 50ms (2400 samples) is pre-silence with mic noise
    silence_samples = int(0.05 * fs)
    raw_mic[:silence_samples] = np.random.normal(0, noise_floor, silence_samples)
    # Rest of the signal contains the sweep
    raw_mic[silence_samples:] = np.random.normal(0, 0.1, fs - silence_samples)

    metrics = verifier.calculate_metrics(h_full, h_linear, 10.0, raw_mic_signal=raw_mic)

    # SNR should be approx 20 * log10(1.0 / noise_floor) = 60dB
    assert 55 < metrics['snr_db'] < 65, f"Expected SNR ~60dB, got {metrics['snr_db']:.1f}dB"


def test_snr_raw_mic_vs_deconvolved_tail():
    """Verify that raw mic signal gives correct SNR while deconvolved tail gives inflated SNR."""
    fs = 48000
    verifier = DSPVerificationTool(fs)

    # Create a synthetic IR: a single spike
    h_linear = np.zeros(fs)
    h_linear[100] = 1.0

    # h_full with near-zero tail (simulating deconvolution noise suppression)
    h_full = h_linear.copy()
    h_full[-int(0.05 * fs):] = np.random.normal(0, 1e-10, int(0.05 * fs))  # Artificially quiet

    # Raw mic signal with realistic noise in pre-silence
    raw_mic = np.zeros(fs)
    real_noise = 0.01  # -40dB
    silence_samples = int(0.05 * fs)
    raw_mic[:silence_samples] = np.random.normal(0, real_noise, silence_samples)
    raw_mic[silence_samples:] = np.random.normal(0, 0.1, fs - silence_samples)

    # Without raw mic - uses deconvolved tail (inflated SNR)
    metrics_no_raw = verifier.calculate_metrics(h_full, h_linear, 10.0)

    # With raw mic - uses pre-silence noise (correct SNR)
    metrics_with_raw = verifier.calculate_metrics(h_full, h_linear, 10.0, raw_mic_signal=raw_mic)

    # The raw mic SNR should be around 40 dB (20*log10(1/0.01))
    assert 35 < metrics_with_raw['snr_db'] < 45, f"Expected ~40dB, got {metrics_with_raw['snr_db']:.1f}dB"

    # The deconvolved-tail SNR should be much higher (incorrectly inflated)
    assert metrics_no_raw['snr_db'] > 100, f"Expected inflated SNR >100dB, got {metrics_no_raw['snr_db']:.1f}dB"

    # Raw mic should give a significantly lower (more realistic) SNR
    assert metrics_with_raw['snr_db'] < metrics_no_raw['snr_db'] - 50


def test_thd_calculation():
    fs = 48000
    verifier = DSPVerificationTool(fs)

    # Create a synthetic IR
    h_linear = np.zeros(fs)
    h_linear[100] = 1.0

    h_full = np.zeros(fs * 2)
    # Put linear part in the middle (approx where it would be in Farina)
    h_full[fs:fs + fs] = h_linear

    # Put some "distortion" in the negative time part (before the linear spike)
    # Farina's method puts harmonics at negative time.
    h_full[1000:1100] = 0.1  # 10% amplitude = -20dB = approx 10% THD in energy

    metrics = verifier.calculate_metrics(h_full, h_linear, 10.0)

    # thd_pct = sqrt(dist_energy / linear_energy) * 100
    # linear_energy = 1.0^2 = 1.0
    # dist_energy = 100 * 0.1^2 = 1.0
    # sqrt(1.0/1.0) * 100 = 100% ? Wait.
    # 0.1 spike is 10% of linear spike.
    # Energy of 100 samples of 0.1 is 100 * 0.01 = 1.0.
    # Energy of 1 sample of 1.0 is 1.0.
    # So yes, 100% THD in this extreme case.

    assert metrics['thd_pct'] > 50


def test_verification_warnings():
    fs = 48000
    verifier = DSPVerificationTool(fs)

    # Case 1: All good
    metrics_good = {'snr_db': 50, 'thd_pct': 0.1, 'psr': 10.0}
    warnings = verifier.verify(metrics_good)
    assert len(warnings) == 0

    # Case 2: Bad SNR
    metrics_noisy = {'snr_db': 20, 'thd_pct': 0.1, 'psr': 10.0}
    warnings = verifier.verify(metrics_noisy)
    assert any("LOW SNR" in w for w in warnings)

    # Case 3: Bad THD
    metrics_distorted = {'snr_db': 50, 'thd_pct': 15.0, 'psr': 10.0}
    warnings = verifier.verify(metrics_distorted)
    assert any("HIGH DISTORTION" in w for w in warnings)

    # Case 4: Bad Alignment
    metrics_smeared = {'snr_db': 50, 'thd_pct': 0.1, 'psr': 2.0}
    warnings = verifier.verify(metrics_smeared)
    assert any("POOR ALIGNMENT" in w for w in warnings)


def test_integration_with_mock_audio(tmp_path):
    from nfs.audio import AudioFactory
    import configparser
    from nfs.datatypes import CylindricalPosition

    # Change working directory to tmp_path to ensure isolation and proper directory creation
    old_cwd = os.getcwd()
    os.chdir(tmp_path)

    try:
        config = configparser.ConfigParser()
        config['audio'] = {
            'mode': 'mock_interface',
            'fs': '48000',
            'in_dev': '0',
            'out_dev': '0',
            'in_ch_mic': '1',
            'in_ch_loop': '0',
            'out_ch_spkr': '0',
            'out_ch_ref': '1',
            'blocksize': '1024',
            'wasapi_exclusive': 'False'
        }

        config['sweep'] = {
            'sweep_dur_s': '0.5',
            'sweep_level_dbfs': '-10',
            'num_sweeps': '1',
            'pre_sil_ms': '50',
            'post_sil_ms': '50',
            'mic_tail_taper_ms': '10',
            'align_to_first_marker': 'True',
            'debug_saves': 'True',
            'H2_TEST_DB': 'None',
            'H3_TEST_DB': 'None',
            'protect_hpf_hz': '0',
            'protect_hpf_order': '4',
            'protect_hpf_correction': 'False',
            'protect_hpf_corr_db_cap': '12.0',
        }

        config['marker'] = {
            'marker_dur_ms': '50',
            'marker_f_lo': '1000',
            'marker_f_hi': '5000',
            'marker_level_dbfs': '-6'
        }

        config_path = tmp_path / "dsp_test_config.ini"
        with open(config_path, "w") as f:
            config.write(f)

        audio = AudioFactory.create(str(config_path))
        pos = CylindricalPosition(100.0, 0.0, 10.0)

        # This will trigger measure_ir -> _run_sweep -> metrics calculation
        # Since it's MockInterfaceAudio, it should have good metrics
        audio.measure_ir(pos, "DSP_TEST")

        # Check if metrics file was created
        # The filename depends on the naming convention (default: dimitri)
        # r100.0 -> r100p0, ph0.0 -> ph0p0, z10.0 -> z10p0
        metrics_file = Path("Recordings/debug/DSP_TEST_r100p0_ph0p0_z10p0_metrics.json")
        assert metrics_file.exists(), f"Metrics file not found at {metrics_file}. Files: {list(Path('Recordings/debug').glob('*'))}"

    finally:
        os.chdir(old_cwd)
