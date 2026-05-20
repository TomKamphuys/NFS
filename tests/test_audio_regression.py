import pytest
import numpy as np
import json
import os
import shutil
import soundfile as sf
from pathlib import Path
import configparser
from nfs.audio import AudioFactory
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


def test_measure_ir_regression(mock_config, tmp_path):
    # Change working directory to tmp_path to avoid polluting project
    old_cwd = os.getcwd()
    os.chdir(tmp_path)

    try:
        # Create Audio object
        audio = AudioFactory.create(str(mock_config))

        # Set session directory
        audio.set_session_directory(tmp_path)

        # Fixed position and ID
        pos = CylindricalPosition(100.0, 0.0, 10.0)
        order_id = "REGRESSION"

        # Set random seed for reproducibility
        np.random.seed(42)

        # Run measurement
        audio.measure_ir(pos, order_id)

        # Expected paths based on naming_convention='dimitri'
        # r100.0 -> r100p0, ph0.0 -> ph0p0, z10.0 -> z10p0
        base_name = "REGRESSION_r100p0_ph0p0_z10p0"

        # Audio class uses self.rec_dir for recordings and self.rec_dir / "debug" for metrics
        # If set_session_directory(tmp_path) is called:
        # self.rec_dir = tmp_path / "Recordings"
        # self.debug_dir = tmp_path / "Recordings" / "debug"

        metrics_file = tmp_path / "Recordings" / "debug" / f"{base_name}_metrics.json"
        ir_file = tmp_path / "Recordings" / f"{base_name}_ir.wav"

        assert metrics_file.exists(), f"Metrics file not found at {metrics_file}. Root files: {list(tmp_path.glob('**/*'))}"
        assert ir_file.exists(), f"IR file not found at {ir_file}."

        with open(metrics_file, "r") as f:
            metrics = json.load(f)

        # These reference values are based on the state as of 2026-04-04 23:16
        # If they change, it means the processing pipeline or hardware simulation has changed.
        # np.random.seed(42) is used for reproducibility.

        # Expected metrics for MockInterfaceAudio with 0.5s sweep and 100ms silences
        assert np.isclose(metrics['snr_db'], 105.38, atol=0.5)
        assert np.isclose(metrics['thd_pct'], 4.94, atol=0.2)
        assert np.isclose(metrics['psr'], 3.57, atol=0.2)
        assert np.isclose(metrics['crest_factor'], 133.20, atol=1.0)

        # Regression test for IR file content
        data, fs_ir = sf.read(str(ir_file))
        assert fs_ir == 48000

        # --- FUNCTIONAL VALIDATION (Robust across platforms) ---
        # 1. Peak Amplitude Check
        peak_amp = np.max(np.abs(data))
        assert np.isclose(peak_amp, 0.2491, atol=1e-3), f"Peak amplitude mismatch: {peak_amp}"

        # 2. RMS Level Check
        rms_level = np.sqrt(np.mean(data**2))
        assert np.isclose(rms_level, 0.00187, atol=1e-4), f"RMS level mismatch: {rms_level}"

        # 3. Frequency Response Check (Flatness in passband)
        # We expect it to be flat between 100Hz and 15kHz within +/- 0.5dB
        n_fft = 2**14
        spec = np.abs(np.fft.rfft(data, n=n_fft))
        freqs = np.fft.rfftfreq(n_fft, 1/fs_ir)
        
        # Normalize to 1kHz
        idx_1k = np.argmin(np.abs(freqs - 1000))
        ref_mag = spec[idx_1k]
        spec_db = 20 * np.log10(spec / (ref_mag + 1e-12))

        # Check a few key frequencies
        test_freqs = [100, 500, 2000, 5000, 10000, 15000]
        for f in test_freqs:
            idx = np.argmin(np.abs(freqs - f))
            # Mock interface has some roll-off from simulated HPF and FIR, but passband is very flat
            assert -0.5 < spec_db[idx] < 0.5, f"Frequency response at {f}Hz is out of bounds: {spec_db[idx]:.2f} dB"

        # Check roll-off at extremes
        idx_20 = np.argmin(np.abs(freqs - 20))
        # 15Hz HPF 1st order should be roughly -3dB at 15Hz, so at 20Hz it's still slightly down
        assert spec_db[idx_20] < 0, f"Expected low-end roll-off at 20Hz, got {spec_db[idx_20]:.2f} dB"

        # 4. Temporal Alignment (Peak position)
        # MockInterfaceAudio has 20ms fixed latency + FIR group delay (12 samples)
        # BUT the DeconvolutionEngine trims the IR so the peak is near the beginning.
        peak_idx = np.argmax(np.abs(data))
        assert 0 <= peak_idx <= 20, f"Peak alignment mismatch: found at index {peak_idx}"

        # 5. Statistical Check
        assert np.isclose(np.std(data), 0.00187, atol=1e-4)

    finally:
        os.chdir(old_cwd)


if __name__ == "__main__":
    # If run directly, generate the reference values
    import sys
    import pytest

    pytest.main([__file__])
