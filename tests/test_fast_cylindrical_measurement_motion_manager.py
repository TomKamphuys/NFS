"""
Tests for :class:`FastCylindricalMeasurementMotionManager`.

These tests are intentionally elaborate: the whole point of the fast motion
manager is to be *faster* than the original sequential manager while remaining
*just as safe*. The tests therefore fall into three groups:

1. Unit tests of the movement strategy (which scanner commands are issued for
   surface moves vs. interior-crossing transitions).

2. A full end-to-end **safety simulation**. The real
   :class:`CylindricalMeasurementPoints` generator drives both the fast and the
   original managers through a ``PathRecordingScanner`` that reconstructs the
   straight-line path each ``G0`` command produces and samples it densely. The
   test asserts that **no** point along **any** path segment ever enters the
   protected interior volume of the measurement grid.

3. A **time-gain** comparison that models the motion time of both managers using
   the same kinematic parameters and asserts (and prints) the speed-up.
"""
import math

import pytest

from nfs.datatypes import CylindricalPosition
from nfs.motion_manager import (
    FastCylindricalMeasurementMotionManager,
    CylindricalMeasurementMotionManager,
    CylindricalNoFlyZone,
)
from nfs.plugins.cylindrical_measurement_points import CylindricalMeasurementPoints


# --------------------------------------------------------------------------- #
# Kinematic model (matches the [grbl_streamer] mock defaults in config.ini).
# --------------------------------------------------------------------------- #
LINEAR_SPEED_MM_S = 300.0
ANGULAR_SPEED_DEG_S = 15.0
# Fixed cost paid per issued command (comms round-trip + accel/decel ramp).
# Every separate G0 that has to fully start and stop costs this on top of the
# pure travel time. This is deliberately conservative (small) so the reported
# speed-up is not inflated by overhead alone.
COMMAND_OVERHEAD_S = 0.20


class PathRecordingScanner:
    """
    A test double for :class:`nfs.scanner.Scanner`.

    It keeps track of the current cylindrical position, reconstructs the
    straight-line machine path produced by each move command, and records every
    segment so the tests can (a) verify safety by sampling the path and
    (b) estimate the motion time.
    """

    def __init__(self, start: CylindricalPosition):
        self._pos = start
        self.segments: list[tuple[CylindricalPosition, CylindricalPosition, str]] = []
        self.commands: list[str] = []

    # -- Scanner API used by the managers ---------------------------------- #
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

    def shutdown(self) -> None:  # pragma: no cover - not exercised
        pass

    # -- helpers ----------------------------------------------------------- #
    def _record(self, target: CylindricalPosition, kind: str) -> None:
        self.segments.append((self._pos, target, kind))
        self.commands.append(kind)
        self._pos = target

    def total_time(self) -> float:
        """Estimate total motion time using the kinematic model."""
        total = 0.0
        for start, end, _kind in self.segments:
            dr = abs(end.r() - start.r())
            dz = abs(end.z() - start.z())
            dt = abs(end.t() - start.t())
            # Simultaneous axes -> the slowest axis governs the travel time.
            travel = max(dr / LINEAR_SPEED_MM_S,
                         dz / LINEAR_SPEED_MM_S,
                         dt / ANGULAR_SPEED_DEG_S)
            total += travel + COMMAND_OVERHEAD_S
        return total


def make_points() -> CylindricalMeasurementPoints:
    """A modestly sized but representative cylindrical grid."""
    return CylindricalMeasurementPoints(
        nr_of_angular_points=8,
        nr_of_radial_cap_points=3,
        nr_of_vertical_points=6,
        cap_spacing=10.0,
        wall_spacing=10.0,
        radius=300.0,
        height=400.0,
    )


def keep_out_predicate(points: CylindricalMeasurementPoints):
    """
    Build a predicate that tells whether a cylindrical position is inside the
    protected interior volume of the measurement grid.

    The protected region is the bulk of the cylinder:
    * radially inside the wall shell (``r < radius - delta_radius``), and
    * vertically between the bottom and top cap bands
      (``cap_spacing < z < height - cap_spacing``).

    All legitimate measurement points lie on the caps or the wall and are thus
    on/outside this region; only a naive traverse across the middle of the grid
    would enter it.
    """
    radius = points._radius
    height = points._height
    delta_radius = points._delta_radius
    cap_spacing = points._cap_spacing
    tol = 1e-6

    keep_out_radius = radius - delta_radius - tol
    z_low = cap_spacing + tol
    z_high = height - cap_spacing - tol

    def inside(pos: CylindricalPosition) -> bool:
        return (pos.r() < keep_out_radius) and (z_low < pos.z() < z_high)

    return inside


def assert_path_is_safe(scanner: PathRecordingScanner,
                        inside, samples: int = 200) -> None:
    """Densely sample every recorded segment and assert none enters keep-out."""
    for start, end, kind in scanner.segments:
        for i in range(samples + 1):
            s = i / samples
            r = start.r() + s * (end.r() - start.r())
            t = start.t() + s * (end.t() - start.t())
            z = start.z() + s * (end.z() - start.z())
            sample = CylindricalPosition(r, t, z)
            assert not inside(sample), (
                f'Unsafe path point {sample} on {kind} segment '
                f'{start} -> {end}'
            )


def make_no_fly_zone(points) -> CylindricalNoFlyZone:
    """Build the cylindrical keep-out zone that matches the grid's interior."""
    return CylindricalNoFlyZone(
        r_wall=points._radius - points._delta_radius,
        z_min=points._cap_spacing,
        z_max=points._height - points._cap_spacing,
    )


def run_manager(manager_cls, points) -> PathRecordingScanner:
    """Drive a manager through the whole grid and return the scanner used."""
    no_fly_zone = make_no_fly_zone(points)
    # Start parked outside the grid, as move_to_safe_starting_radius would leave us.
    scanner = PathRecordingScanner(
        CylindricalPosition(no_fly_zone.retract_radius, -180.0, 0.0))
    manager = manager_cls(scanner, points, no_fly_zone)
    manager.move_to_safe_starting_radius()

    guard = 0
    # total_points() is a documented approximation of the real count, so allow
    # a generous safety margin before declaring a non-terminating generator.
    max_points = points.total_points() * 2 + 50
    while not manager.ready():
        manager.next()
        guard += 1
        assert guard <= max_points, 'Point generator did not terminate'
    return scanner


# =========================================================================== #
# 1. Unit tests of the movement strategy.
# =========================================================================== #
class _StubPoints:
    """Minimal MeasurementPoints stub returning a scripted point."""

    def __init__(self, point):
        self._point = point

    def next(self):
        return self._point

    def ready(self):
        return False

    def reset(self):
        pass

    def total_points(self):
        return 1


def test_surface_move_is_single_combined_command():
    scanner = PathRecordingScanner(CylindricalPosition(100.0, 0.0, 10.0))
    target = CylindricalPosition(150.0, 0.0, 20.0)
    # Both endpoints sit above the zone -> the move is executed directly.
    manager = FastCylindricalMeasurementMotionManager(
        scanner, _StubPoints(target),
        no_fly_zone=CylindricalNoFlyZone(r_wall=300.0, z_min=0.0, z_max=5.0))

    result = manager.next()

    assert result == target
    # Exactly one command, and it moves all changed axes at once.
    assert scanner.commands == ['combined']
    assert scanner.get_position() == target


def test_no_move_when_already_at_target():
    start = CylindricalPosition(150.0, 0.0, 20.0)
    scanner = PathRecordingScanner(start)
    manager = FastCylindricalMeasurementMotionManager(
        scanner, _StubPoints(start),
        no_fly_zone=CylindricalNoFlyZone(r_wall=300.0, z_min=0.0, z_max=5.0))

    manager.next()

    assert scanner.commands == []


def test_evasive_move_routes_around_the_outside():
    # A blocked move where the arm starts inside the wall radius and must first
    # retract to the zone's wall radius (the retract radius).
    start = CylindricalPosition(150.0, 0.0, 200.0)  # inside the keep-out band
    target = CylindricalPosition(50.0, 45.0, 0.0)   # (min_radius, new theta, 0)
    scanner = PathRecordingScanner(start)
    manager = FastCylindricalMeasurementMotionManager(
        scanner, _StubPoints(target),
        no_fly_zone=CylindricalNoFlyZone(r_wall=300.0, z_min=0.0, z_max=400.0))

    manager.next()

    # 1) retract to the wall radius, 2) combined slew there, 3) move in.
    assert scanner.commands == ['radial', 'combined', 'radial']

    retract, slew, move_in = scanner.segments
    # Step 1 goes out to the wall radius, angle/z unchanged.
    assert retract[1].r() == pytest.approx(300.0)
    assert retract[1].z() == pytest.approx(200.0)
    # Step 2 rotates + drops Z while staying at the wall radius.
    assert slew[0].r() == pytest.approx(300.0)
    assert slew[1].r() == pytest.approx(300.0)
    assert slew[1].t() == pytest.approx(45.0)
    assert slew[1].z() == pytest.approx(0.0)
    # Step 3 comes back in to the target radius at the target z.
    assert move_in[1].r() == pytest.approx(50.0)
    assert move_in[1].z() == pytest.approx(0.0)


def test_evasive_move_skips_retract_when_already_safe():
    # Arm already at the wall radius (end of top cap) -> no retract needed.
    start = CylindricalPosition(300.0, 0.0, 400.0)
    target = CylindricalPosition(50.0, 45.0, 0.0)
    scanner = PathRecordingScanner(start)
    manager = FastCylindricalMeasurementMotionManager(
        scanner, _StubPoints(target),
        no_fly_zone=CylindricalNoFlyZone(r_wall=300.0, z_min=0.0, z_max=400.0))

    manager.next()

    # Already at the wall radius: only the slew and the move in.
    assert scanner.commands == ['combined', 'radial']


# =========================================================================== #
# 2. Full end-to-end safety simulation.
# =========================================================================== #
def test_fast_manager_full_scan_never_enters_grid():
    points = make_points()
    inside = keep_out_predicate(points)
    scanner = run_manager(FastCylindricalMeasurementMotionManager, points)
    assert_path_is_safe(scanner, inside)


def test_original_manager_full_scan_never_enters_grid():
    # Sanity check: the original manager is safe too (baseline for the model).
    points = make_points()
    inside = keep_out_predicate(points)
    scanner = run_manager(CylindricalMeasurementMotionManager, points)
    assert_path_is_safe(scanner, inside)


def test_direct_traverse_would_be_unsafe():
    """
    Negative control: prove the keep-out predicate actually has teeth.

    A single direct combined move for the interior-crossing transition (the move
    the fast manager deliberately routes around) *does* pierce the grid.
    """
    points = make_points()
    inside = keep_out_predicate(points)
    scanner = PathRecordingScanner(CylindricalPosition(300.0, 0.0, 400.0))
    scanner.move_to(50.0, 45.0, 0.0)  # naive shortcut across the middle
    with pytest.raises(AssertionError):
        assert_path_is_safe(scanner, inside)


def test_fast_manager_visits_every_point_in_order():
    """The fast manager must not change which points are measured."""
    points_fast = make_points()
    no_fly_zone = make_no_fly_zone(points_fast)
    scanner_fast = PathRecordingScanner(
        CylindricalPosition(no_fly_zone.retract_radius, -180.0, 0.0))
    fast = FastCylindricalMeasurementMotionManager(scanner_fast, points_fast, no_fly_zone)
    fast.move_to_safe_starting_radius()

    points_ref = make_points()
    visited_fast = []
    visited_ref = []
    while not fast.ready():
        visited_fast.append(fast.next())
    # Reproduce the reference sequence straight from the generator.
    while not points_ref.ready():
        visited_ref.append(points_ref.next())

    assert [str(p) for p in visited_fast] == [str(p) for p in visited_ref]


# =========================================================================== #
# 3. Time-gain comparison.
# =========================================================================== #
def test_fast_manager_is_significantly_faster(capsys):
    points_fast = make_points()
    points_slow = make_points()

    scanner_fast = run_manager(
        FastCylindricalMeasurementMotionManager, points_fast)
    scanner_slow = run_manager(
        CylindricalMeasurementMotionManager, points_slow)

    t_fast = scanner_fast.total_time()
    t_slow = scanner_slow.total_time()
    speedup = t_slow / t_fast

    with capsys.disabled():
        print('\n--- Cylindrical motion time-gain (modelled) ---')
        print(f'linear speed          : {LINEAR_SPEED_MM_S:.0f} mm/s')
        print(f'angular speed         : {ANGULAR_SPEED_DEG_S:.0f} deg/s')
        print(f'per-command overhead  : {COMMAND_OVERHEAD_S:.2f} s')
        print(f'commands (original)   : {len(scanner_slow.commands)}')
        print(f'commands (fast)       : {len(scanner_fast.commands)}')
        print(f'time (original)       : {t_slow:8.1f} s  ({t_slow/60:5.2f} min)')
        print(f'time (fast)           : {t_fast:8.1f} s  ({t_fast/60:5.2f} min)')
        print(f'speed-up              : {speedup:5.2f}x  '
              f'({(1 - t_fast / t_slow) * 100:4.1f}% faster)')

    # The fast manager issues far fewer commands ...
    assert len(scanner_fast.commands) < len(scanner_slow.commands)
    # ... and is meaningfully faster.
    assert speedup > 1.5


def test_time_model_is_deterministic():
    points = make_points()
    a = run_manager(FastCylindricalMeasurementMotionManager, points).total_time()
    points = make_points()
    b = run_manager(FastCylindricalMeasurementMotionManager, points).total_time()
    assert math.isclose(a, b)
