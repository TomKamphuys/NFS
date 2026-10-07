from unittest.mock import Mock

import pytest

from nfs.datatypes import CylindricalPosition as Point
from nfs.motion_manager import CylindricalNoFlyZone, SphericalNoFlyZone, MotionManagerFactory
from nfs.point_ordering import OptimizedMeasurementPoints, travel_distance, linear_distance


class Points:
    def __init__(self, points):
        self.points = points
        self.reset()

    def reset(self):
        self.index = 0

    def ready(self):
        return self.index == len(self.points)

    def next(self):
        point = self.points[self.index]
        self.index += 1
        return point

    def total_points(self):
        return len(self.points)


@pytest.mark.parametrize('manager', ['CylindricalMeasurementMotionManager',
                                   'FastCylindricalMeasurementMotionManager',
                                   'SphericalMeasurementMotionManager'])
def test_shorter_route_preserves_every_entry_and_reset(manager):
    zone = SphericalNoFlyZone(10) if manager.startswith('Spherical') else CylindricalNoFlyZone(10, -10, 10)
    points = [Point(100, angle, 0) for angle in (0, 100, 10, 90, 10)]
    result = OptimizedMeasurementPoints(Points(points), manager, zone)
    ordered = [result.next() for _ in points]
    assert result.ready()
    assert result.total_points() == len(points)
    assert sorted(map(id, ordered)) == sorted(map(id, points))
    assert ordered[0] is points[0]
    def length(route):
        return sum(travel_distance(a, b, manager, zone) for a, b in zip(route, route[1:]))
    assert length(ordered) < length(points)
    result.reset()
    assert result.next() is points[0]


def test_boundary_is_not_a_shortcut():
    assert linear_distance((100, -180, 0), (100, 180, 0)) == pytest.approx(1200)


@pytest.mark.parametrize('manager', ['CylindricalMeasurementMotionManager',
                                   'FastCylindricalMeasurementMotionManager',
                                   'SphericalMeasurementMotionManager'])
@pytest.mark.parametrize('radius', [20, 100, 500])
def test_rotation_has_fixed_cost(manager, radius):
    zone = SphericalNoFlyZone(10) if manager.startswith('Spherical') else CylindricalNoFlyZone(10, -10, 10)
    assert travel_distance(Point(radius, 0, 0), Point(radius, 90, 0),
                           manager, zone) == pytest.approx(300)


def test_combined_move_adds_linear_and_rotation_costs():
    assert linear_distance((20, 0, 0), (50, 90, 40)) == pytest.approx(350)


@pytest.mark.parametrize('manager', ['CylindricalMeasurementMotionManager',
                                   'FastCylindricalMeasurementMotionManager',
                                   'SphericalMeasurementMotionManager'])
def test_small_radius_prefers_linear_move_over_rotation(manager):
    zone = SphericalNoFlyZone(10) if manager.startswith('Spherical') else CylindricalNoFlyZone(10, -10, 10)
    points = [Point(20, 0, 0), Point(20, 90, 0), Point(120, 0, 0)]
    result = OptimizedMeasurementPoints(Points(points), manager, zone)
    assert [result.next() for _ in points] == [points[0], points[2], points[1]]


def test_safe_detour_is_counted():
    zone = CylindricalNoFlyZone(100, 0, 100)
    assert travel_distance(Point(10, 0, 0), Point(10, 0, 100),
                           'FastCylindricalMeasurementMotionManager', zone) == pytest.approx(280)


@pytest.mark.parametrize('point', [Point(1, 0, 5), Point(100, 181, 0),
                                  Point(100, -181, 0), Point(float('nan'), 0, 0)])
def test_invalid_grid_rejected(point):
    with pytest.raises(ValueError):
        OptimizedMeasurementPoints(Points([point]), 'FastCylindricalMeasurementMotionManager',
                                   CylindricalNoFlyZone(10, 0, 10))


@pytest.mark.parametrize('count', [0, 1, 2])
def test_small_sets(count):
    result = OptimizedMeasurementPoints(Points([Point(100, 0, 0)] * count),
                                       'FastCylindricalMeasurementMotionManager',
                                       CylindricalNoFlyZone(10, 0, 10))
    assert result.total_points() == count
    for _ in range(count):
        result.next()
    assert result.ready()


@pytest.mark.parametrize('enabled', [False, True])
def test_factory_opt_in(tmp_path, monkeypatch, enabled):
    source = Points([Point(100, angle, 0) for angle in (0, 100, 10)])
    create = Mock(return_value=source)
    monkeypatch.setattr('nfs.motion_manager.factory.create', create)
    config = tmp_path / 'config.ini'
    config.write_text('[motion_manager]\ntype = FastCylindricalMeasurementMotionManager\n'
                      'no_fly_radius = 10\nno_fly_z_min = 0\nno_fly_z_max = 10\n'
                      f'optimize_point_order = {enabled}\n', encoding='utf-8')
    manager = MotionManagerFactory.create(str(config), 'motion_manager', Mock())
    assert isinstance(manager._measurement_points, OptimizedMeasurementPoints) == enabled
    assert 'optimize_point_order' not in create.call_args.args[0]