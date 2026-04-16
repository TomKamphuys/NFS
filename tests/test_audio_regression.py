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
        'PROTECT_HPF_HZ': '0',
        'PROTECT_HPF_ORDER': '4',
        'PROTECT_HPF_PHASE': 'min'
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
        assert np.isclose(metrics['snr_db'], 101.85, atol=0.1)
        assert np.isclose(metrics['thd_pct'], 4.85, atol=0.1)
        assert np.isclose(metrics['psr'], 3.57, atol=0.1)
        assert np.isclose(metrics['crest_factor'], 89.72, atol=0.1)

        # Regression test for IR file content
        data, fs_ir = sf.read(str(ir_file))
        assert fs_ir == 48000

        # Known reference values (Calculated 2026-04-04)
        # Hash of the raw float32 data from the WAV file
        ref_hash = "fc7f91782417c6cc1effc0e27828f45edf416c6decf09b30a7e06f72e3e796b1"
        ref_std = 0.00585583
        
        import hashlib
        actual_hash = hashlib.sha256(data.tobytes()).hexdigest()
        
        # Verify bit-identical (or extremely close if cross-platform float differences occur)
        # Note: with fixed seed and float32 wav, we expect exact match.
        assert actual_hash == ref_hash, f"IR Hash mismatch! Expected {ref_hash}, got {actual_hash}"
        assert np.isclose(np.std(data), ref_std, atol=1e-6)

    finally:
        os.chdir(old_cwd)


if __name__ == "__main__":
    # If run directly, generate the reference values
    import sys
    import pytest

    pytest.main([__file__])
