"""
Tests for the safety-critical ``need_to_do_evasive_move`` of
:class:`nfs.plugins.file_measurement_points.FileMeasurementPoints`.

The fast cylindrical motion manager only performs a direct simultaneous move
when the point generator guarantees the straight path stays out of the grid's
protected interior. ``FileMeasurementPoints`` now deduces the largest cylinder
that fits inside all file points (bottom-cap plane, top-cap plane and wall
radius) and only requests an evasive move when a straight transition could cut
through that interior. These tests verify the manager is both *fast* (direct
moves along the surfaces) and *safe* (never enters the interior).
"""
import csv

import pytest

from nfs.datatypes import CylindricalPosition
from nfs.motion_manager import FastCylindricalMeasurementMotionManager
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


def _simple_cylinder_points() -> list[CylindricalPosition]:
    """
    A clean, ordered cylinder grid: bottom cap, wall, top cap for two angles.

    Bottom cap at z=0, top cap at z=400, wall radius 300. All points lie on a
    surface, so a well-behaved manager only needs to evade the interior-crossing
    jump from the top cap back down to the next angle's bottom cap.
    """
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


TOL = 1e-6


def test_keep_out_cylinder_is_deduced_from_points(tmp_path):
    pts = _simple_cylinder_points()
    fp = _make_file_points(tmp_path, pts)
    assert fp._z_bottom == pytest.approx(0.0)
    assert fp._z_top == pytest.approx(400.0)
    assert fp._r_wall == pytest.approx(300.0)


def test_surface_moves_are_direct_and_only_interior_jumps_evade(tmp_path):
    """
    Walking the two-angle grid: moves that stay on the bottom cap, wall or top
    cap must be direct (fast); only the top-cap -> next-angle bottom-cap jump is
    evasive.
    """
    pts = _simple_cylinder_points()
    fp = _make_file_points(tmp_path, pts)

    flags = []
    while not fp.ready():
        fp.next()
        flags.append(fp.need_to_do_evasive_move())

    # First move is conservatively evasive (unknown start position).
    assert flags[0] is True
    evasive_count = sum(flags)
    # Only the first move plus the single interior-crossing transition evade.
    assert evasive_count == 2, f'expected 2 evasive moves, got {evasive_count}'
    # The evasive (non-first) one is the jump from top cap back to bottom cap.
    evasive_indices = [i for i, f in enumerate(flags) if f]
    jump_index = evasive_indices[1]
    prev = pts[jump_index - 1]
    curr = pts[jump_index]
    assert prev.z() == pytest.approx(400.0)  # end of top cap
    assert curr.z() == pytest.approx(0.0)    # start of next bottom cap


def test_full_scan_through_fast_manager_stays_out_of_interior(tmp_path):
    """
    Drive the fast manager over the file grid and prove the invariant directly:
    every direct move keeps the straight path out of the deduced interior, and
    evasive moves route around the outside at ``safe_radius``.
    """
    pts = _simple_cylinder_points()
    fp = _make_file_points(tmp_path, pts)
    safe_radius = fp._r_wall + 50.0

    def inside(pos: CylindricalPosition) -> bool:
        return (pos.r() < fp._r_wall - 1e-3) and (
            fp._z_bottom + 1e-3 < pos.z() < fp._z_top - 1e-3
        )

    scanner = PathRecordingScanner(CylindricalPosition(safe_radius, -180.0, 0.0))
    manager = FastCylindricalMeasurementMotionManager(scanner, fp, safe_radius)

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


def _spherical_shell_points() -> list[CylindricalPosition]:
    """
    A curved (spherical/HALS-like) shell around the axis.

    The measured surface is a half-circle in the (r, z) plane of radius R,
    swept over a couple of angles. The key property -- and the one that broke
    the old algorithm -- is that the points nearest the axis (small r) live near
    the poles (|z| close to R), yet their z is still strictly between the global
    z extremes. The genuine empty interior is a fat cylinder in the middle, not
    the needle the old code produced.
    """
    import math

    R = 300.0
    pts: list[CylindricalPosition] = []
    for angle in (-180.0, -90.0):
        for deg in range(0, 181, 15):  # polar angle from bottom pole to top pole
            a = math.radians(deg)
            r = R * math.sin(a)
            z = -R * math.cos(a)
            pts.append(CylindricalPosition(r, angle, z))
    return pts


def test_curved_shell_keep_out_is_a_fat_cylinder_not_a_needle(tmp_path):
    """
    Regression test for the spherical-shell degeneracy.

    Previously ``r_wall`` collapsed to the innermost radius of the whole cloud
    (a point near a pole), leaving both caps empty and the interior essentially
    unprotected. The largest-empty-cylinder search must instead report a wall
    radius that is a large fraction of the shell radius, with real cap planes
    inside the global z extremes.
    """
    pts = _spherical_shell_points()
    fp = _make_file_points(tmp_path, pts)

    z_min = min(p.z() for p in pts)
    z_max = max(p.z() for p in pts)

    # A fat cylinder: the wall must be well away from the axis, not the tiny
    # near-pole radius the old code produced.
    assert fp._r_wall > 150.0, f'wall collapsed to {fp._r_wall}'
    # The caps must sit strictly inside the global z extremes (real cap planes).
    assert fp._z_bottom > z_min + TOL
    assert fp._z_top < z_max - TOL
    assert fp._z_bottom < fp._z_top

    # No measurement point may lie strictly inside the deduced cylinder.
    for p in pts:
        strictly_inside = (
            p.r() < fp._r_wall - TOL
            and fp._z_bottom + TOL < p.z() < fp._z_top - TOL
        )
        assert not strictly_inside, f'point {p} ended up inside the keep-out cylinder'


def test_curved_shell_flags_interior_crossing_move_as_evasive(tmp_path):
    """
    On the curved shell, a straight move that dives through the middle of the
    interior (equator to equator across the axis region) must be flagged as
    evasive; with the old needle-thin cylinder it was wrongly considered safe.
    """
    pts = _spherical_shell_points()
    fp = _make_file_points(tmp_path, pts)

    # Two equatorial points on opposite conceptual sides, both at mid-height and
    # inside the wall radius when interpolated -> the straight path cuts the
    # interior. Build them explicitly and drive the evasive check.
    fp._points = [
        CylindricalPosition(fp._r_wall - 10.0, -180.0, 0.0),
        CylindricalPosition(fp._r_wall - 10.0, -90.0, 0.0),
    ]
    fp._current_index = 2  # pretend the second point was just served
    assert fp.need_to_do_evasive_move() is True


def test_missing_file_is_safe_and_reports_no_evasion():
    points = FileMeasurementPoints('this_file_does_not_exist.csv')
    assert points.total_points() == 0
    assert points.need_to_do_evasive_move() is False
