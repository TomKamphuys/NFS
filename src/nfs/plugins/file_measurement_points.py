import csv
from pathlib import Path

from loguru import logger

from nfs.datatypes import CylindricalPosition


class FileMeasurementPoints:
    """Base class for file measurement points plugins."""

    def __init__(self, filename: str,
                 homing_gap: float = 0.0,
                 pole_gap: float = 0.0):
        self._homing_gap = float(homing_gap)
        self._pole_gap = float(pole_gap)
        self._points: list[CylindricalPosition] = []
        self._current_index = 0
        self._ready = False

        if not Path(filename).exists():
            logger.warning(f"Measurement points file not found: '{filename}'")
            return

        with open(filename, newline="") as f:  # Open CSV file
            reader = csv.DictReader(f)  # Parse header-based rows
            coords = list(reader)  # Convert to list for indexing

        # Loop over coordinates, up to MAX_POINTS
        for idx, row in enumerate(coords, start=1):
            # Extract coordinate data (as text from CSV)
            r_xy_mm = float(row.get("r_xy_mm"))  # Radial distance in XY plane (mm)
            phi_deg = float(row.get("phi_deg"))  # Azimuth angle (degrees)
            z_mm = float(row.get("z_mm"))  # Height position (mm)
            if self._remove_point_inside_homing_area(phi_deg) or self._remove_point_inside_speaker_stand(r_xy_mm):
                continue
            self._points.append(CylindricalPosition(r_xy_mm, phi_deg, z_mm))

        logger.info(f"Read {len(self._points)} points from input file '{filename}' (out of {len(coords)} rows in file)")

        self._compute_keep_out_cylinder()

    # Tolerance (mm) used when deciding whether a point lies on/outside a
    # keep-out boundary.
    TOLERANCE = 0.1

    def _compute_keep_out_cylinder(self) -> None:
        """
        Deduce the largest cylinder that fits *inside* all measurement points,
        i.e., the biggest cylinder such that **every** point lies on or outside
        its surface. The protected interior (where the device under test sits) is
        bounded by a wall radius ``r_wall`` and two cap planes ``z_bottom`` and
        ``z_top``.

        The three bounds cannot be picked independently, because real grids are
        rarely a clean wall with two perfectly flat caps. On a curved shell (for
        example, a spherical/HALS grid) the points closest to the axis sit near
        the poles, yet their Z is still strictly between the global Z extremes.
        Classifying points as "wall" purely by ``z_min < z < z_max`` therefore
        lumps those inner pole points into the wall and collapses ``r_wall`` to
        the innermost radius of the whole cloud, leaving both caps empty and
        producing a needle-thin cylinder that fails to protect the interior.

        Instead, we search for the genuinely *largest* empty cylinder. For every
        candidate wall radius (each distinct point radius) we treat the points
        strictly inside that radius as cap points: the highest such point below
        mid-height fixes ``z_bottom`` and the lowest such point above mid-height
        fixes ``z_top``. By construction no point then lies strictly inside the
        candidate cylinder. Among all candidates we keep the one with the largest
        volume ``r_wall**2 * (z_top - z_bottom)``.

        A straight move between two points is guaranteed to stay out of this
        interior whenever both endpoints are on the same side of one of these
        boundaries (both below the bottom, both above the top, or both at/outside
        the wall radius). Only then may the fast manager do a direct move.
        """
        if not self._points:
            self._z_bottom = 0.0
            self._z_top = 0.0
            self._r_wall = 0.0
            return

        z_min = min(p.z() for p in self._points)
        z_max = max(p.z() for p in self._points)
        z_mid = 0.5 * (z_min + z_max)

        best_volume = -1.0
        best: tuple[float, float, float] | None = None

        # Try every distinct point radius as the wall of the empty cylinder.
        for r_wall in sorted({p.r() for p in self._points}):
            # Points nearer the axis than this wall would sit inside the shell,
            # so they define how far the caps may reach in from top and bottom.
            inner = [p for p in self._points if p.r() < r_wall]
            lower = [p.z() for p in inner if p.z() < z_mid]
            upper = [p.z() for p in inner if p.z() >= z_mid]

            z_bottom = max(lower) if lower else z_min
            z_top = min(upper) if upper else z_max
            if z_top <= z_bottom:
                # Some inner point would fall strictly inside: not a valid,
                # non-degenerate cylinder for this radius.
                continue

            volume = r_wall * r_wall * (z_top - z_bottom)
            if volume > best_volume:
                best_volume = volume
                best = (r_wall, z_bottom, z_top)

        if best is None:
            # Degenerate grid (e.g., all points at the same height): no interior.
            self._r_wall = min(p.r() for p in self._points)
            self._z_bottom = z_min
            self._z_top = z_max
            return

        self._r_wall, self._z_bottom, self._z_top = best

    def next(self) -> CylindricalPosition:
        if self._current_index < len(self._points):
            point = self._points[self._current_index]
            self._current_index += 1
            return point
        raise StopIteration("No more points")

    def get_radius(self) -> float:
        """
        Returns the first loaded radius for compatibility with motion managers
        that ask measurement-points objects for a nominal radius.

        :return: A radius (mm).
        """
        if self._points:
            return self._points[0].r()
        return 0.0

    def reset(self) -> None:
        self._current_index = 0

    def ready(self) -> bool:
        return self._current_index >= len(self._points)

    def total_points(self) -> int:
        return len(self._points)

    def need_to_do_evasive_move(self) -> bool:
        """
        Report whether the move reaching the point most recently returned by
        :meth:`next` must be routed safely around the outside of the grid.

        The largest cylinder fitting inside all points is deduced from the file
        (bottom-cap plane, top-cap plane, wall radius). A direct move between the
        previous and the current point is safe -- and therefore *not* evasive --
        when both points lie on the same side of one keep-out boundary:

        * both at or below the bottom cap (``z <= z_bottom``),
        * both at or above the top cap (``z >= z_top``), or
        * both at or outside the wall radius (``r >= r_wall``).

        In those cases the straight, linearly interpolated path never enters the
        protected interior. Any other transition may cut through the interior and
        is flagged as evasive.

        :return: True when the current move must be a safe evasive maneuver.
        """
        # No point served yet -> no move to protect.
        if not (1 <= self._current_index <= len(self._points)):
            return False
        # First move: we do not know the machine's start position, so play safe.
        if self._current_index < 2:
            return True

        prev = self._points[self._current_index - 2]
        curr = self._points[self._current_index - 1]

        both_below = prev.z() <= self._z_bottom and curr.z() <= self._z_bottom
        both_above = prev.z() >= self._z_top and curr.z() >= self._z_top
        both_outside = prev.r() >= self._r_wall and curr.r() >= self._r_wall

        return not (both_below or both_above or both_outside)

    def _remove_point_inside_speaker_stand(self, r_cyl) -> bool:
        # everything in mm and degrees
        return r_cyl < (self._pole_gap / 2.0)

    def _remove_point_inside_homing_area(self, theta_cyl) -> bool:

        limit = 180.0 - (self._homing_gap / 2.0)   # Calculate the boundary limit (e.g., 175 degrees if gap is 10)
        return abs(theta_cyl) > limit  # Using abs() catches both the positive and negative boundaries


def register(factory) -> None:
    factory.register("FileMeasurementPoints", FileMeasurementPoints)
