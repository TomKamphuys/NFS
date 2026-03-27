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


def test_integration_with_mock_audio():
    from nfs.audio import AudioFactory
    import configparser

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
        'PROTECT_HPF_HZ': '0',
        'PROTECT_HPF_ORDER': '4',
        'PROTECT_HPF_PHASE': 'min'
    }

    config['marker'] = {
        'marker_dur_ms': '50',
        'marker_f_lo': '1000',
        'marker_f_hi': '5000',
        'marker_level_dbfs': '-6'
    }

    config_path = "tests/dsp_test_config.ini"
    with open(config_path, "w") as f:
        config.write(f)

    try:
        audio = AudioFactory.create(config_path)
        from nfs.datatypes import CylindricalPosition
        pos = CylindricalPosition(100, 0, 10)

        # This will trigger measure_ir -> _run_sweep -> metrics calculation
        # Since it's MockInterfaceAudio, it should have good metrics
        audio.measure_ir(pos, "DSP_TEST")

        # Check if metrics file was created
        # The filename depends on the naming convention (default: dimitri)
        metrics_file = Path("Recordings/debug/DSP_TEST_r100_ph0_z10_metrics.json")
        assert metrics_file.exists()

    finally:
        pass

    #     if os.path.exists(config_path):
    #         os.remove(config_path)
    #     if os.path.exists("Recordings"):
    #         shutil.rmtree("Recordings")
    #     if os.path.exists("Distortion"):
    #         shutil.rmtree("Distortion")
