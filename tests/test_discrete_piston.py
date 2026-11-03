import math

import numpy as np

from nfs.datatypes import CylindricalPosition
from nfs.utils.spatial import DiscretePistonSpeakerModel


def test_piston_response_is_equal_at_front_and_rear():
    model = DiscretePistonSpeakerModel(hf_taper_hz=0.0)
    fs = 48000
    positions = [
        CylindricalPosition(2000.0, angle, 0.0)
        for angle in (0.0, 180.0, -180.0)
    ]
    responses = [model.compute_frequency_response(fs, position) for position in positions]

    bins = [1000, 5000, 10000]
    for response in responses[1:]:
        np.testing.assert_allclose(response[bins], responses[0][bins], rtol=1e-12, atol=1e-12)


def test_frequency_array_helper_matches_independent_monopole_sum():
    model = DiscretePistonSpeakerModel(
        piston_radius_m=0.04,
        piston_point_count=17,
        hf_taper_hz=0.0,
    )
    position = CylindricalPosition(750.0, 37.0, 120.0)
    frequencies = np.array([500.0, 1000.0, 5000.0])

    actual = model._compute_green_function_response(position, frequencies)

    phi = math.radians(position.t())
    mic_position = np.array([
        position.r() / 1000.0 * math.cos(phi),
        position.r() / 1000.0 * math.sin(phi),
        position.z() / 1000.0,
    ])
    distances = np.linalg.norm(model._piston_points - mic_position, axis=1)
    wave_numbers = 2.0 * np.pi * frequencies / 343.0
    expected = np.mean(
        np.exp(-1j * wave_numbers[:, None] * distances[None, :])
        / (4.0 * np.pi * distances[None, :]),
        axis=1,
    )

    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)


def test_full_fft_response_uses_same_frequency_array_sum():
    fs = 48000
    model = DiscretePistonSpeakerModel(volume_gain=0.75, hf_taper_hz=0.0)
    position = CylindricalPosition(800.0, 23.0, -60.0)
    full = model.compute_frequency_response(fs, position)
    bins = np.array([500, 1000, 5000])

    sparse = model._compute_green_function_response(position, bins.astype(float))

    np.testing.assert_allclose(full[bins], sparse * model._volume_gain, rtol=1e-12, atol=1e-12)