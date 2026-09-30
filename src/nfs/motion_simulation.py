"""
Off-line simulation of the motion managers for visual verification.

This module drives the *real* motion manager (built through
:class:`nfs.motion_manager.MotionManagerFactory` from the active configuration)
over a light-weight :class:`RecordingScanner`. The recording scanner never
touches hardware; it only reconstructs the machine path each move command would
produce in the ``(r, z)`` plane and stores it as a list of densely sampled
segments.

The result is a :class:`MotionSimulation` describing:

* every path segment (with its sampled polyline) so a viewer can draw the whole
  motion -- not merely the measurement end points; and
* the configured no-fly (keep-out) zone geometry so the viewer can render it and
  a human can confirm the arm never enters it.

Keeping this logic free of any GUI dependency makes it directly unit-testable and
reusable by the Qt motion-preview window.
"""
from __future__ import annotations

import configparser
import math
from dataclasses import dataclass, field

from loguru import logger

from .datatypes import CylindricalPosition
from .motion_manager import (
    CylindricalNoFlyZone,
    MotionManagerFactory,
    SphericalNoFlyZone,
    UNSAFE_POINT_ABORT,
    UnsafeMeasurementPointError,
)


# Sampling resolution used to reconstruct a move as a polyline.
_MM_PER_SAMPLE = 5.0        # one sample every 5 mm of straight travel
_ARC_DEG_PER_SAMPLE = 2.0   # one sample every 2 degrees of arc travel
_MIN_SAMPLES = 2


@dataclass
class MotionSegment:
    """
    A single reconstructed machine move in the ``(r, z)`` plane.

    :ivar kind: The move primitive that produced it (``'planar'``, ``'radial'``,
        ``'vertical'``, ``'angular'``, ``'combined'``, ``'cw_arc'`` or
        ``'ccw_arc'``).
    :ivar r: Sampled radius values (mm) along the move.
    :ivar z: Sampled height values (mm) along the move, aligned with ``r``.
    :ivar evasive: True when the move is part of an evasive (keep-out avoiding)
        maneuver rather than a straight measurement transition.
    """

    kind: str
    r: list[float]
    z: list[float]
    evasive: bool = False


@dataclass
class MotionSimulation:
    """
    The full reconstructed motion together with the no-fly zone geometry.

    :ivar segments: Every reconstructed move, in execution order.
    :ivar points: The measurement end points, in visitation order.
    :ivar zone_kind: ``'cylindrical'``, ``'spherical'`` or ``'none'``.
    :ivar zone: A dictionary with the zone geometry (keys depend on ``zone_kind``).
    :ivar unsafe_points: Measurement points that lie inside the no-fly zone and
        would therefore cause a collision. These are what a viewer should flag.
    :ivar unsafe_point_policy: The configured policy for unsafe points
        (``'abort'`` or ``'skip'``), so the viewer can explain the consequence.
    :ivar aborted: True when the configured policy is ``abort`` and at least one
        unsafe point was found, i.e. the real scan would refuse to run.
    """

    segments: list[MotionSegment] = field(default_factory=list)
    points: list[CylindricalPosition] = field(default_factory=list)
    zone_kind: str = "none"
    zone: dict = field(default_factory=dict)
    unsafe_points: list[CylindricalPosition] = field(default_factory=list)
    unsafe_point_policy: str = UNSAFE_POINT_ABORT
    aborted: bool = False

    def flatten(self) -> list[tuple[float, float]]:
        """
        Return every sampled ``(r, z)`` point of the whole motion in order.

        Consecutive segments share a start/end point; the shared duplicate is
        dropped so the result is a continuous polyline.
        """
        path: list[tuple[float, float]] = []
        for segment in self.segments:
            for r, z in zip(segment.r, segment.z):
                point = (r, z)
                if path and path[-1] == point:
                    continue
                path.append(point)
        return path


class RecordingScanner:
    """
    A hardware-free stand-in for :class:`nfs.scanner.Scanner`.

    It tracks the current cylindrical position and, for every move command the
    motion manager issues, reconstructs and stores the machine path in the
    ``(r, z)`` plane. Straight moves are sampled linearly; ``G02``/``G03`` arcs
    are sampled along the constant-length circle centred on the origin, exactly
    the way grbl(hal) executes them.
    """

    def __init__(self, start: CylindricalPosition):
        self._pos = start
        self.segments: list[MotionSegment] = []
        self._evasive = False

    # -- Scanner API used by the motion managers --------------------------- #
    def get_position(self) -> CylindricalPosition:
        return self._pos

    def planar_move_to(self, r: float, z: float) -> None:
        self._straight(CylindricalPosition(r, self._pos.t(), z), "planar")

    def radial_move_to(self, r: float) -> None:
        self._straight(CylindricalPosition(r, self._pos.t(), self._pos.z()), "radial")

    def vertical_move_to(self, z: float) -> None:
        self._straight(CylindricalPosition(self._pos.r(), self._pos.t(), z), "vertical")

    def angular_move_to(self, angle: float) -> None:
        # A pure azimuth change does not move the arm in the (r, z) plane, but we
        # still record it (as a zero-length segment) so the step count matches.
        self._straight(CylindricalPosition(self._pos.r(), angle, self._pos.z()), "angular")

    def move_to(self, r: float, angle: float, z: float) -> None:
        self._straight(CylindricalPosition(r, angle, z), "combined")

    def cw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        self._arc(CylindricalPosition(r, self._pos.t(), z), radius, "cw_arc")

    def ccw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        self._arc(CylindricalPosition(r, self._pos.t(), z), radius, "ccw_arc")

    def shutdown(self) -> None:  # pragma: no cover - not exercised in simulation
        pass

    # -- evasive-move marking --------------------------------------------- #
    def mark_evasive(self, evasive: bool) -> None:
        """Tag the moves recorded until the next call as (non-)evasive."""
        self._evasive = evasive

    # -- reconstruction helpers ------------------------------------------- #
    def _straight(self, target: CylindricalPosition, kind: str) -> None:
        start = self._pos
        distance = math.hypot(target.r() - start.r(), target.z() - start.z())
        samples = max(_MIN_SAMPLES, int(distance / _MM_PER_SAMPLE) + 1)
        rs, zs = [], []
        for i in range(samples):
            f = i / (samples - 1)
            rs.append(start.r() + f * (target.r() - start.r()))
            zs.append(start.z() + f * (target.z() - start.z()))
        self.segments.append(MotionSegment(kind, rs, zs, self._evasive))
        self._pos = target

    def _arc(self, target: CylindricalPosition, radius: float, kind: str) -> None:
        start = self._pos
        angle0 = math.atan2(start.z(), start.r())
        angle1 = math.atan2(target.z(), target.r())
        sweep_deg = abs(math.degrees(angle1 - angle0))
        samples = max(_MIN_SAMPLES, int(sweep_deg / _ARC_DEG_PER_SAMPLE) + 1)
        length = radius if radius else target.length()
        rs, zs = [], []
        for i in range(samples):
            f = i / (samples - 1)
            angle = angle0 + f * (angle1 - angle0)
            rs.append(length * math.cos(angle))
            zs.append(length * math.sin(angle))
        # Pin the exact endpoints (guard against tiny rounding drift).
        rs[0], zs[0] = start.r(), start.z()
        rs[-1], zs[-1] = target.r(), target.z()
        self.segments.append(MotionSegment(kind, rs, zs, self._evasive))
        self._pos = target


def _resolve_motion_manager_section(config_file: str) -> str:
    """
    Resolve the active motion-manager section name from the config file.

    The application never hard-codes the section name: :class:`nfs.nfs.
    NearFieldScannerFactory` looks it up through ``[nfs] motion_manager``. The
    preview must do exactly the same, otherwise it could read a different (or a
    non-existent) section and draw a no-fly zone that does not match the one the
    scan actually uses. Falls back to ``"motion_manager"`` when the key is
    absent, matching the default configuration.

    :param config_file: Path to the ``config.ini`` used by the application.
    :return: The motion-manager section name to build the manager from.
    """
    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read(config_file)
    return config_parser.get("nfs", "motion_manager", fallback="motion_manager")


def simulate_motion(
    config_file: str,
    section: str | None = None,
    start: CylindricalPosition | None = None,
) -> MotionSimulation:
    """
    Build the configured motion manager and record its full motion off-line.

    :param config_file: Path to the ``config.ini`` used by the application.
    :param section: The motion-manager configuration section. When ``None`` (the
        default), the section is resolved from ``[nfs] motion_manager`` exactly
        the way the running application does, so the preview always reflects the
        active configuration.
    :param start: Optional initial arm position; defaults to the origin.
    :return: A :class:`MotionSimulation` describing every move and the no-fly zone.
    """
    if section is None:
        section = _resolve_motion_manager_section(config_file)
    scanner = RecordingScanner(start or CylindricalPosition(0.0, 0.0, 0.0))
    manager = MotionManagerFactory.create(config_file, section, scanner)

    simulation = MotionSimulation()
    _capture_zone(manager, simulation)
    simulation.unsafe_point_policy = getattr(
        manager, "_unsafe_point_policy", UNSAFE_POINT_ABORT
    )
    # Independently flag every measurement point that lies inside the zone, so
    # the viewer can highlight them regardless of the abort/skip policy (an
    # ``abort`` run would otherwise stop at the very first unsafe point).
    simulation.unsafe_points = _collect_unsafe_points(manager)
    simulation.aborted = bool(
        simulation.unsafe_points
        and simulation.unsafe_point_policy == UNSAFE_POINT_ABORT
    )

    manager.move_to_safe_starting_radius()
    guard = 0
    max_iterations = manager.total_points() + 5
    try:
        while not manager.ready():
            position = manager.next()
            simulation.points.append(position)
            guard += 1
            if guard > max_iterations:
                logger.warning(
                    "Motion simulation exceeded the expected number of points; "
                    "stopping to avoid an infinite loop."
                )
                break
    except UnsafeMeasurementPointError as exc:
        # With the ``abort`` policy the real scan stops at the first unsafe
        # point. Record what we have so the viewer can still show the planned
        # motion up to that point together with the offending point(s).
        logger.warning(f"Motion simulation aborted at an unsafe point: {exc}")
        simulation.aborted = True

    simulation.segments = scanner.segments
    return simulation


def _collect_unsafe_points(manager) -> list[CylindricalPosition]:
    """
    Return every measurement point that lies inside the manager's no-fly zone.

    The measurement-point sequence is walked in isolation (and rewound
    afterwards) so this diagnostic pass has no effect on the subsequent motion
    recording. Points are checked with the very same ``contains`` test the
    running scanner uses, so the viewer flags exactly what the scan would reject.

    :param manager: The built motion manager.
    :return: The unsafe measurement points, in visitation order.
    """
    zone = getattr(manager, "_no_fly_zone", None)
    points = getattr(manager, "_measurement_points", None)
    if zone is None or points is None:
        return []

    unsafe: list[CylindricalPosition] = []
    points.reset()
    guard = 0
    max_iterations = points.total_points() + 5
    while not points.ready():
        position = points.next()
        if zone.contains(position):
            unsafe.append(position)
        guard += 1
        if guard > max_iterations:
            break
    points.reset()
    return unsafe


def _capture_zone(manager, simulation: MotionSimulation) -> None:
    """Read the no-fly zone geometry off the built manager, if any."""
    zone = getattr(manager, "_no_fly_zone", None)
    if isinstance(zone, CylindricalNoFlyZone):
        if zone.r_wall > 0.0 and zone.z_max > zone.z_min:
            simulation.zone_kind = "cylindrical"
            simulation.zone = {
                "r_wall": zone.r_wall,
                "z_min": zone.z_min,
                "z_max": zone.z_max,
            }
    elif isinstance(zone, SphericalNoFlyZone):
        if zone.radius > 0.0:
            simulation.zone_kind = "spherical"
            simulation.zone = {"radius": zone.radius}
