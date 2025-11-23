import pytest
from src.nfs.datatypes import CylindricalPosition
from nfs.plugins.spherical_measurement_points_arcs_random import SphericalMeasurementPointsArcsRandom


def test_spherical_initialization():
    smpar = SphericalMeasurementPointsArcsRandom(nr_of_points=100, wall_spacing=1.0, radius=100.0)
    assert smpar.get_radius() == 100.0


def test_spherical_next():
    smpar = SphericalMeasurementPointsArcsRandom(nr_of_points=100, wall_spacing=1.0, radius=100.0)
    position = smpar.next()
    assert isinstance(position, CylindricalPosition)
    assert position.r() >= 30.0  # Ensuring valid cylindrical radius


def test_spherical_reset():
    smpar = SphericalMeasurementPointsArcsRandom(nr_of_points=10, wall_spacing=1.0, radius=100.0)
    smpar.next()
    smpar.reset()
    assert smpar.ready() is False
    first_position = smpar.next()
    smpar.reset()
    reset_position = smpar.next()
    assert first_position == reset_position


def test_spherical_ready_flag():
    smpar = SphericalMeasurementPointsArcsRandom(nr_of_points=3, wall_spacing=1.0, radius=100.0)
    for _ in range(smpar._actual_nr_of_points):
        smpar.next()
    assert smpar.ready() is True


def test_spherical_next_out_of_bounds():
    smpar = SphericalMeasurementPointsArcsRandom(nr_of_points=5, wall_spacing=1.0, radius=100.0)
    for _ in range(smpar._actual_nr_of_points):
        smpar.next()
    with pytest.raises(IndexError):
        smpar.next()
