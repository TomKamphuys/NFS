"""The capture and IR components can be used without the measurement coordinator."""

import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest


@pytest.mark.parametrize('module', ['audio_capture', 'audio_signals', 'sweep_to_ir'])
def test_component_import_does_not_load_coordinator_or_audio_driver(module, tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    code = (f'import nfs.{module}; import sys; '
            'assert "nfs.audio" not in sys.modules; assert "sounddevice" not in sys.modules')
    if module == 'sweep_to_ir':
        code += '; assert "nfs.audio_capture" not in sys.modules'
    subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env, check=True, timeout=20)


def test_ir_component_reprocesses_arrays_without_device_or_coordinator():
    from nfs.audio_capture import AudioCapture
    from nfs.audio_signals import MarkerGenerator, SweepGenerator
    from nfs.sweep_to_ir import AlignmentEngine, DeconvolutionEngine, SweepToIR

    hw = {'fs': 48000}
    cap = dict(num_sweeps=2, sweep_level_dbfs=-16, pre_sil_ms=100,
               post_sil_ms=100, loopback_correction=False)
    sweep = SweepGenerator(48000, .25, 1, -16)
    marker = MarkerGenerator(48000, 100, (500, 5000), -16)
    acquisition = AudioCapture(hw, cap, sweep, marker)
    plan = acquisition._prepare_sweep()
    processor = SweepToIR(hw, cap, sweep, AlignmentEngine(48000, 2, False, 10, 100),
                          DeconvolutionEngine(48000))
    accepted = processor._finish_sweep(plan, plan['tx_speaker'], plan['tx_loop'])
    first = processor._process_capture(accepted)
    second = processor._process_capture(accepted)
    for before, after in zip(first, second):
        np.testing.assert_array_equal(before, after)


def test_legacy_dsp_imports_reexport_the_same_classes():
    from nfs import audio, audio_signals, sweep_to_ir

    assert audio.SweepGenerator is audio_signals.SweepGenerator
    assert audio.DeconvolutionEngine is sweep_to_ir.DeconvolutionEngine
    assert audio.AlignmentEngine is sweep_to_ir.AlignmentEngine
