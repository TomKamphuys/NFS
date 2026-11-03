from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from nfs.audio import AudioFactory, MockInterfaceAudio
from nfs.datatypes import CylindricalPosition
from nfs.nfs import NearFieldScanner
from nfs.utils.spatial import DiscretePistonSpeakerModel, SphericalHarmonicSpeakerModel


FS = 48000
POSITIONS = (
    CylindricalPosition(400.0, 0.0, 0.0),
    CylindricalPosition(400.0, 90.0, 0.0),
)


def _write_mock_config(path: Path, spatial_model: str | None = None) -> Path:
    model_setting = f"spatial_model = {spatial_model}\n" if spatial_model is not None else ""
    path.write_text(
        """[audio]
mode = mock_interface
fs = 48000
in_ch_mic = 0
in_ch_loop = 1
out_ch_spkr = 0
out_ch_ref = 1
blocksize = 256
wasapi_exclusive = False

[sweep]
sweep_dur_s = 0.25
sweep_level_dbfs = -10
num_sweeps = 1
pre_sil_ms = 50
post_sil_ms = 50
mic_tail_taper_ms = 10
align_to_first_marker = True
debug_saves = False
H2_TEST_DB = None
H3_TEST_DB = None
PROTECT_HPF_HZ = None
PROTECT_HPF_ORDER = 4
PROTECT_HPF_CORRECTION = False
PROTECT_HPF_CORR_DB_CAP = 12.0
""" + model_setting,
        encoding="utf-8",
    )
    return path


class _PositionScanner:
    def __init__(self, positions):
        self._positions = iter(positions)

    def get_position(self):
        return next(self._positions)


class _PositionMotionManager:
    def __init__(self, count):
        self._count = count
        self._index = -1

    def move_to_safe_starting_radius(self):
        pass

    def total_points(self):
        return self._count

    def ready(self):
        return self._index >= self._count - 1

    def next(self):
        self._index += 1

    def reset(self):
        pass

    def move_to_park_position(self):
        pass


def _load_measurement_irs(directory: Path) -> list[np.ndarray]:
    return [
        sf.read(path, dtype="float64")[0]
        for path in sorted(directory.rglob("*_ir.wav"))
    ]


def _run_capture_flow(config_path: Path, output_path: Path, *, measurement_set: bool):
    output_path.mkdir(parents=True, exist_ok=True)
    audio = AudioFactory.create(str(config_path))
    assert isinstance(audio, MockInterfaceAudio)
    scanner = _PositionScanner(POSITIONS)
    motion_manager = _PositionMotionManager(len(POSITIONS))
    nfs = NearFieldScanner(
        scanner,
        audio,
        motion_manager,
        position_log_file=str(output_path / "measurement_positions.csv"),
    )
    nfs.set_project_directory(output_path)

    if measurement_set:
        nfs.take_measurement_set("spatial_model_regression", overwrite=True)
        output_directory = output_path / "measurement_set"
    else:
        for _ in POSITIONS:
            nfs.take_single_measurement()
        output_directory = output_path / "single_measurements"

    return audio._speaker_model, _load_measurement_irs(output_directory)


@pytest.mark.parametrize(
    ("configured_value", "expected_model"),
    [
        ("none", None),
        ("spherical_harmonic", SphericalHarmonicSpeakerModel),
        ("discrete_piston", DiscretePistonSpeakerModel),
        ("true", SphericalHarmonicSpeakerModel),
        ("1", SphericalHarmonicSpeakerModel),
        ("yes", SphericalHarmonicSpeakerModel),
        (None, None),
    ],
)
def test_audio_factory_selects_spatial_model(configured_value, expected_model, tmp_path):
    config_path = _write_mock_config(tmp_path / "config.ini", configured_value)

    audio = AudioFactory.create(str(config_path))

    assert isinstance(audio, MockInterfaceAudio)
    if expected_model is None:
        assert audio._speaker_model is None
    else:
        assert isinstance(audio._speaker_model, expected_model)


@pytest.mark.parametrize("spatial_model", ["none", "spherical_harmonic", "discrete_piston"])
def test_single_and_set_measurements_use_selected_spatial_model(
    spatial_model, tmp_path
):
    config_path = _write_mock_config(tmp_path / "config.ini", spatial_model)
    random_state = np.random.get_state()
    try:
        np.random.seed(1729)
        single_model, single_irs = _run_capture_flow(
            config_path, tmp_path / "single_flow", measurement_set=False
        )
        np.random.seed(1729)
        set_model, set_irs = _run_capture_flow(
            config_path, tmp_path / "set_flow", measurement_set=True
        )
    finally:
        np.random.set_state(random_state)

    assert len(single_irs) == len(set_irs) == 2
    assert type(single_model) is type(set_model)
    if spatial_model == "none":
        assert single_model is None
        for ir in (single_irs, set_irs):
            front_spectrum, side_spectrum = (np.abs(np.fft.rfft(wave)) for wave in ir)
            frequency_mask = (np.fft.rfftfreq(len(ir[0]), 1 / FS) >= 500) & (
                np.fft.rfftfreq(len(ir[0]), 1 / FS) <= 18000
            )
            front_spectrum /= np.maximum(np.max(front_spectrum[frequency_mask]), 1e-12)
            side_spectrum /= np.maximum(np.max(side_spectrum[frequency_mask]), 1e-12)
            np.testing.assert_allclose(
                front_spectrum[frequency_mask],
                side_spectrum[frequency_mask],
                rtol=0.05,
                atol=2e-4,
            )
    else:
        assert single_model is not None
        for measurements in (single_irs, set_irs):
            front_spectrum, side_spectrum = (np.abs(np.fft.rfft(wave)) for wave in measurements)
            frequencies = np.fft.rfftfreq(len(measurements[0]), 1 / FS)
            frequency_mask = (frequencies >= 500) & (frequencies <= 18000)
            response_difference_db = 20 * np.log10(
                np.maximum(front_spectrum[frequency_mask], 1e-12)
                / np.maximum(side_spectrum[frequency_mask], 1e-12)
            )
            assert np.max(np.abs(response_difference_db)) > 2.0

    for single_ir, set_ir in zip(single_irs, set_irs):
        np.testing.assert_allclose(single_ir, set_ir, rtol=0, atol=0)
