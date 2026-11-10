import pytest
import numpy as np
import json
import soundfile as sf
import configparser
from scipy import signal
from nfs.audio import AudioFactory, SINE_RMS_FROM_PEAK, _LINEAR_IR_ORIGIN_SAMPLE as IR_ORIGIN
from nfs.datatypes import CylindricalPosition


@pytest.fixture
def mock_config(tmp_path):
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
        'loopback_correction': 'False',
        'sweep_dur_s': '0.5',
        'sweep_level_dbfs': '-10',
        'num_sweeps': '1',
        'pre_sil_ms': '100',
        'post_sil_ms': '100',
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

    config_path = tmp_path / "regression_test_config.ini"
    with open(config_path, "w") as f:
        config.write(f)
    return config_path


def test_measure_ir_regression(mock_config, tmp_path, monkeypatch):
    """Verify the complete export against the known simulated transfer function."""
    monkeypatch.chdir(tmp_path)
    # Keep the existing deterministic mock noise without altering other tests' RNG.
    rng_state = np.random.get_state()
    try:
        np.random.seed(42)
        audio = AudioFactory.create(str(mock_config))
        audio.set_session_directory(tmp_path)
        result = audio.measure_ir(CylindricalPosition(100.0, 0.0, 10.0), "REGRESSION")

        base_name = "REGRESSION_r100p0_ph0p0_z10p0"
        metrics_file = tmp_path / "debug" / f"{base_name}_metrics.json"
        ir_file = tmp_path / f"{base_name}_ir.wav"

        assert metrics_file.exists(), (
            f"Metrics file not found at {metrics_file}. Root files: {list(tmp_path.glob('**/*'))}"
        )
        assert ir_file.exists(), f"IR file not found at {ir_file}."

        with open(metrics_file, "r") as f:
            json.load(f)

        _, fs_ir = sf.read(str(ir_file))
        assert fs_ir == 48000
    finally:
        np.random.set_state(rng_state)

    base = "REGRESSION_r100p0_ph0p0_z10p0"
    data, fs = sf.read(tmp_path / f"{base}_ir.wav", dtype="float32")
    np.testing.assert_array_equal(data, result["ir_linear"])
    assert fs == audio.hw["fs"]
    assert sf.info(tmp_path / f"{base}_ir.wav").subtype == "FLOAT"
    # Predict the waveform independently of the deconvolver. Alignment removes
    # the common FIR group delay, but it must preserve its pre/post ringing.
    expected = np.zeros(len(data))
    expected[IR_ORIGIN-12:IR_ORIGIN+13] = signal.firwin(25, 0.45 * fs, fs=fs)
    for cutoff in (15,):  # Mock coupling capacitor only.
        expected = signal.sosfilt(
            signal.butter(1, cutoff, fs=fs, btype="highpass", output="sos"), expected
        )
    expected *= 10 ** (-10 / 20) * SINE_RMS_FROM_PEAK
    np.testing.assert_allclose(data[:9600], expected[:9600], atol=3e-5, rtol=0)
    assert np.argmax(np.abs(data)) == IR_ORIGIN

    freqs = np.fft.rfftfreq(16384, 1 / fs)
    band = (freqs >= 100) & (freqs <= 18000)
    actual_spectrum = np.fft.rfft(data, 16384)
    expected_spectrum = np.fft.rfft(expected, 16384)
    error_db = 20 * np.log10(np.abs(actual_spectrum[band] / expected_spectrum[band]))
    assert np.max(np.abs(error_db)) < 0.1

    metrics = json.loads((tmp_path / "debug" / f"{base}_metrics.json").read_text())
    # These are heuristics, not calibrated hardware SNR/THD specifications.
    assert all(np.isfinite(metrics[key]) for key in ("snr_db", "thd_pct", "psr", "crest_factor"))
