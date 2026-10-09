import numpy as np
import pandas as pd
import pytest
from scipy.integrate import cumulative_trapezoid

from grid_generator.grid_gen import generate_balanced_cylinder, generate_measurement_grid


@pytest.mark.parametrize("radius,height,thickness", [(200, 500, 50), (400, 100, 0), (50, 800, 20)])
def test_balanced_shell_is_exact_unique_quantized_and_repeatable(radius, height, thickness):
    kwargs = dict(radius_mm=radius, height_mm=height, num_points=1200,
                  thickness_mm=thickness, bottom_cutoff_mm=30,
                  phi_min_deg=-170, phi_max_deg=160, z_offset_mm=450)
    grid = generate_balanced_cylinder(**kwargs)
    pd.testing.assert_frame_equal(grid, generate_balanced_cylinder(**kwargs))
    assert len(grid) == 1200
    assert not grid.duplicated().any()
    r, phi, z = grid.to_numpy().T
    assert np.all(r == np.round(r))
    assert np.allclose(phi * 10, np.round(phi * 10))
    assert np.all(z == np.round(z))
    assert np.all((r >= radius) | (abs(z - 450) >= height / 2))
    assert np.all((r > 0) & (r <= radius + thickness))
    assert np.all(abs(z - 450) <= height / 2 + thickness)
    assert np.all((phi >= -170) & (phi <= 160))
    assert not np.any((z - 450 <= -height / 2) & (r <= 30))


def test_balanced_direction_and_azimuth_follow_weighted_cdfs():
    grid = generate_balanced_cylinder(200, 500, 12000, azimuth_density_ratio=4)
    r, phi, z = grid.to_numpy().T
    mu = z / np.hypot(r, z)
    nodes = np.linspace(-1, 1, 100001)
    with np.errstate(divide="ignore"):
        a = np.minimum(200 / np.sqrt(1 - nodes**2), 250 / abs(nodes))
        b = np.minimum(250 / np.sqrt(1 - nodes**2), 300 / abs(nodes))
    polar = cumulative_trapezoid(a * b, nodes, initial=0)
    polar /= polar[-1]
    for threshold in [-.8, -.4, 0, .4, .8]:
        assert abs(np.mean(mu <= threshold) - np.interp(threshold, nodes, polar)) < .01
    angles = np.linspace(-180, 180, 16385)
    half = np.radians(angles) / 2
    azimuth = cumulative_trapezoid(.5 / (.25 * np.cos(half)**2 + np.sin(half)**2), angles, initial=0)
    azimuth /= azimuth[-1]
    for threshold in [-120, -60, 0, 60, 120]:
        assert abs(np.mean(phi <= threshold) - np.interp(threshold, angles, azimuth)) < .005


@pytest.mark.parametrize("fraction", [0, 1, .25])
def test_cap_override_extremes(fraction):
    grid = generate_balanced_cylinder(200, 500, 1000, thickness_mm=0, cap_fraction=fraction)
    caps = abs(grid.z_mm) == 250
    assert abs(caps.mean() - fraction) < .015


def test_integer_translation_and_wrapper_do_not_shift_twice():
    base = generate_balanced_cylinder(200, 500, 1000)
    shifted = generate_balanced_cylinder(200, 500, 1000, z_offset_mm=300)
    np.testing.assert_array_equal(base.z_mm + 300, shifted.z_mm)
    pd.testing.assert_frame_equal(base.iloc[:, :2], shifted.iloc[:, :2])
    wrapped = generate_measurement_grid(num_points=1000, top_crit_pos=(200, 0, 550),
                                        bot_crit_pos=(0, 0, 50))
    pd.testing.assert_frame_equal(wrapped.iloc[:, :3], shifted)
    assert "method=balanced" in set(wrapped.gen_settings)
    assert "seed=42" in set(wrapped.gen_settings)


@pytest.mark.parametrize("kwargs", [dict(num_points=True), dict(seed=True), dict(seed=-1),
    dict(num_points=1.5), dict(radius_mm=0), dict(height_mm=float("nan")),
    dict(thickness_mm=-1), dict(azimuth_density_ratio=.5), dict(phi_max_deg=181),
    dict(cap_fraction=1.1), dict(radius_mm=.1, thickness_mm=0)])
def test_invalid_balanced_settings(kwargs):
    settings = dict(radius_mm=200, height_mm=500, num_points=10)
    settings.update(kwargs)
    with pytest.raises(ValueError):
        generate_balanced_cylinder(**settings)


def test_legacy_can_be_selected(monkeypatch):
    import grid_generator.grid_gen as module
    monkeypatch.setattr(module, "generate_balanced_cylinder", lambda *a, **k: pytest.fail("balanced called"))
    grid = generate_measurement_grid(cyl_radius_mm=200, cyl_height_mm=500,
            bottom_cutoff_mm=30, num_points=100, balanced_spherical_angular_coverage=False)
    assert "method=legacy" in set(grid.gen_settings)
