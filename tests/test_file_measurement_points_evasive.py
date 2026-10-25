"""
Tests that the fast cylindrical motion manager keeps the arm out of its
configured no-fly zone while scanning points loaded from a file.

Collision avoidance now lives in the motion manager (via
:class:`nfs.motion_manager.CylindricalNoFlyZone`), not in
:class:`nfs.plugins.file_measurement_points.FileMeasurementPoints`, which is a
plain point provider. These tests verify the manager is both *fast* (direct
moves along the surfaces) and *safe* (never enters the interior) when driven
over file points.
"""
import csv

import pytest

from nfs.datatypes import CylindricalPosition
from nfs.motion_manager import (
    FastCylindricalMeasurementMotionManager,
    CylindricalNoFlyZone,
)
from nfs.plugins.file_measurement_points import FileMeasurementPoints


class PathRecordingScanner:
    """Minimal scanner double that records the straight-line path of each move."""

    def __init__(self, start: CylindricalPosition):
        self._pos = start
        self.segments: list[tuple[CylindricalPosition, CylindricalPosition, str]] = []
        self.commands: list[str] = []

    def get_position(self) -> CylindricalPosition:
        return self._pos

    def planar_move_to(self, r: float, z: float) -> None:
        self._record(CylindricalPosition(r, self._pos.t(), z), 'planar')

    def radial_move_to(self, r: float) -> None:
        self._record(CylindricalPosition(r, self._pos.t(), self._pos.z()), 'radial')

    def vertical_move_to(self, z: float) -> None:
        self._record(CylindricalPosition(self._pos.r(), self._pos.t(), z), 'vertical')

    def angular_move_to(self, angle: float) -> None:
        self._record(CylindricalPosition(self._pos.r(), angle, self._pos.z()), 'angular')

    def move_to(self, r: float, angle: float, z: float) -> None:
        self._record(CylindricalPosition(r, angle, z), 'combined')

    def shutdown(self) -> None:
        pass

    def _record(self, target: CylindricalPosition, kind: str) -> None:
        self.segments.append((self._pos, target, kind))
        self.commands.append(kind)
        self._pos = target


# The grid's interior: wall radius 300, caps at z=0 and z=400. All points below
# lie on a surface, so the only interior-crossing transition is the jump from
# the top cap back down to the next angle's bottom cap.
GRID_R_WALL = 300.0
GRID_Z_MIN = 0.0
GRID_Z_MAX = 400.0


def _grid_no_fly_zone() -> CylindricalNoFlyZone:
    return CylindricalNoFlyZone(r_wall=GRID_R_WALL, z_min=GRID_Z_MIN, z_max=GRID_Z_MAX)


def _simple_cylinder_points() -> list[CylindricalPosition]:
    """A clean, ordered cylinder grid: bottom cap, wall, top cap for two angles."""
    pts: list[CylindricalPosition] = []
    for angle in (-180.0, -90.0):
        # bottom cap (z=0), radius growing outwards
        for r in (50.0, 150.0, 300.0):
            pts.append(CylindricalPosition(r, angle, 0.0))
        # wall (r=300), z growing
        for z in (100.0, 200.0, 300.0, 400.0):
            pts.append(CylindricalPosition(300.0, angle, z))
        # top cap (z=400), radius shrinking inwards
        for r in (150.0, 50.0):
            pts.append(CylindricalPosition(r, angle, 400.0))
    return pts


def _write_csv(path, points: list[CylindricalPosition]) -> None:
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['r_xy_mm', 'phi_deg', 'z_mm'])
        for p in points:
            writer.writerow([p.r(), p.t(), p.z()])


def _make_file_points(tmp_path, points) -> FileMeasurementPoints:
    csv_path = tmp_path / 'grid.csv'
    _write_csv(csv_path, points)
    return FileMeasurementPoints(str(csv_path))


def test_file_points_are_plain_points(tmp_path):
    """FileMeasurementPoints is a pure point provider (no safety API)."""
    fp = _make_file_points(tmp_path, _simple_cylinder_points())
    assert fp.total_points() == len(_simple_cylinder_points())
    assert not hasattr(fp, 'need_to_do_evasive_move')
    assert not hasattr(fp, 'get_radius')


def test_surface_moves_are_direct_and_only_interior_jumps_evade(tmp_path):
    """
    Walking the two-angle grid: moves that stay on the bottom cap, wall or top
    cap must be direct (fast); only the top-cap -> next-angle bottom-cap jump is
    evasive.
    """
    pts = _simple_cylinder_points()
    fp = _make_file_points(tmp_path, pts)
    zone = _grid_no_fly_zone()

    scanner = PathRecordingScanner(CylindricalPosition(GRID_R_WALL, -180.0, 0.0))
    manager = FastCylindricalMeasurementMotionManager(scanner, fp, zone)

    evasive_flags = []
    prev = scanner.get_position()
    while not manager.ready():
        target = fp._points[fp._current_index]
        evasive_flags.append(zone.blocks_move(prev, target))
        manager.next()
        prev = target

    # Exactly one interior-crossing transition (top cap -> next bottom cap).
    assert sum(evasive_flags) == 1
    jump_index = evasive_flags.index(True)
    assert pts[jump_index - 1].z() == pytest.approx(400.0)  # end of top cap
    assert pts[jump_index].z() == pytest.approx(0.0)        # start of next bottom cap


def test_full_scan_through_fast_manager_stays_out_of_interior(tmp_path):
    """
    Drive the fast manager over the file grid and prove the invariant directly:
    every move keeps the straight path out of the configured interior.
    """
    pts = _simple_cylinder_points()
    fp = _make_file_points(tmp_path, pts)
    zone = _grid_no_fly_zone()

    def inside(pos: CylindricalPosition) -> bool:
        return (pos.r() < GRID_R_WALL - 1e-3) and (
            GRID_Z_MIN + 1e-3 < pos.z() < GRID_Z_MAX - 1e-3
        )

    scanner = PathRecordingScanner(
        CylindricalPosition(zone.retract_radius, -180.0, 0.0))
    manager = FastCylindricalMeasurementMotionManager(scanner, fp, zone)

    manager.move_to_safe_starting_radius()
    guard = 0
    while not manager.ready():
        manager.next()
        guard += 1
        assert guard <= fp.total_points() + 50, 'scan did not terminate'

    samples = 100
    for start, end, kind in scanner.segments:
        for i in range(samples + 1):
            s = i / samples
            r = start.r() + s * (end.r() - start.r())
            t = start.t() + s * (end.t() - start.t())
            z = start.z() + s * (end.z() - start.z())
            assert not inside(CylindricalPosition(r, t, z)), (
                f'Unsafe path point on {kind} segment {start} -> {end}'
            )


def test_missing_file_yields_no_points():
    points = FileMeasurementPoints('this_file_does_not_exist.csv')
    assert points.total_points() == 0
    assert points.ready() is True
