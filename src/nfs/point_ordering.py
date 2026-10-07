"""Weighted travel ordering without changing the motion manager's safe paths."""
import math

from loguru import logger


ROTATION_MM_PER_DEGREE = 300.0 / 90.0


def linear_distance(start, end):
    """Linear R/Z travel plus a radius-independent azimuth penalty."""
    r, t, z = start
    return math.hypot(end[0] - r, end[2] - z) + abs(end[1] - t) * ROTATION_MM_PER_DEGREE


def travel_distance(start, end, manager_type, zone):
    r, t, z = start.r(), start.t(), start.z()
    R, T, Z = end.r(), end.t(), end.z()
    angular = abs(T - t) * ROTATION_MM_PER_DEGREE
    if manager_type == 'SphericalMeasurementMotionManager':
        return (angular + abs(end.length() - start.length())
                + end.length() * abs(math.atan2(Z, R) - math.atan2(z, r)))
    safe = zone.retract_radius
    if manager_type == 'CylindricalMeasurementMotionManager':
        if abs(Z - z) <= 0.1:
            return angular + abs(R - r)
        outer = max(r, safe)
        return angular + outer - r + abs(Z - z) + abs(R - outer)
    if not zone.blocks_move(start, end):
        return linear_distance((r, t, z), (R, T, Z))
    outer = max(r, safe)
    return (outer - r + linear_distance((outer, t, z), (safe, T, Z))
            + abs(R - safe))


class OptimizedMeasurementPoints:
    """Deterministic nearest-neighbour candidate, retained only if shorter.

    Keep the original first point to preserve the scan's approach move. All
    entries (including duplicates) survive; invalid grids fail before movement.
    """

    def __init__(self, source, manager_type, zone):
        self._points = []
        source.reset()
        while not source.ready():
            point = source.next()
            if (not all(math.isfinite(v) for v in (point.r(), point.t(), point.z()))
                    or point.r() < 0 or not -180 <= point.t() <= 180):
                raise ValueError('Optimization requires finite points, R >= 0 and angles within [-180, 180].')
            if zone.contains(point, tolerance=0):
                raise ValueError('Cannot optimize: a measurement point is inside the safety zone. No points may be skipped.')
            self._points.append(point)
        source.reset()
        self._index = 0
        if len(self._points) < 3:
            return
        def cost(a, b):
            return travel_distance(a, b, manager_type, zone)
        def length(points):
            return sum(cost(a, b) for a, b in zip(points, points[1:]))
        remaining = list(range(1, len(self._points)))
        candidate = [self._points[0]]
        while remaining:
            index = min(remaining, key=lambda i: cost(candidate[-1], self._points[i]))
            candidate.append(self._points[index])
            remaining.remove(index)
        original_length, candidate_length = length(self._points), length(candidate)
        if candidate_length < original_length:
            self._points = candidate
        logger.info(f'Measurement ordering: {original_length:.1f} -> '
                    f'{min(original_length, candidate_length):.1f} mm-equivalent weighted travel')

    def next(self):
        point = self._points[self._index]
        self._index += 1
        return point

    def ready(self):
        return self._index >= len(self._points)

    def reset(self):
        self._index = 0

    def total_points(self):
        return len(self._points)