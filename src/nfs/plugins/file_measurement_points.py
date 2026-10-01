import csv
from pathlib import Path

from loguru import logger

from nfs.datatypes import CylindricalPosition


class FileMeasurementPoints:
    """Base class for file measurement points plugins."""

    def __init__(self, filename: str,
                 homing_gap: float = 0.0,
                 pole_gap: float = 0.0):
        """
        Load measurement points from a CSV file.

        The CSV is expected to contain ``r_xy_mm``, ``phi_deg`` and ``z_mm``
        columns. Points that fall inside the homing area or inside the speaker
        stand are filtered out while loading. A missing file yields an empty set.

        :param filename: Path to the CSV file holding the measurement points.
        :param homing_gap: Angular gap (degrees) around +/-180 to keep clear.
        :param pole_gap: Diameter (mm) of the central pole to keep clear.
        """
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

    def next(self) -> CylindricalPosition:
        """
        Return the next loaded measurement point.

        :return: The next measurement point in cylindrical coordinates.
        :rtype: CylindricalPosition
        :raises StopIteration: When all points have been served.
        """
        if self._current_index < len(self._points):
            point = self._points[self._current_index]
            self._current_index += 1
            return point
        raise StopIteration("No more points")

    def reset(self) -> None:
        """
        Rewind the cursor so iteration restarts from the first point.
        """
        self._current_index = 0

    def ready(self) -> bool:
        """
        Report whether all loaded points have been served.

        :return: True once the sequence is exhausted, False otherwise.
        :rtype: bool
        """
        return self._current_index >= len(self._points)

    def total_points(self) -> int:
        """
        Return the number of points loaded from the file.

        :return: The total number of measurement points.
        :rtype: int
        """
        return len(self._points)

    def _remove_point_inside_speaker_stand(self, r_cyl) -> bool:
        """
        Decide whether a point lies inside the central speaker stand/pole.

        :param r_cyl: The point's radial distance (mm).
        :return: True if the point is inside the pole radius and must be dropped.
        :rtype: bool
        """
        # everything in mm and degrees
        return r_cyl < (self._pole_gap / 2.0)

    def _remove_point_inside_homing_area(self, theta_cyl) -> bool:
        """
        Decide whether a point lies inside the angular homing keep-out area.

        :param theta_cyl: The point's angular coordinate (degrees).
        :return: True if the point is inside the homing gap and must be dropped.
        :rtype: bool
        """

        limit = 180.0 - (self._homing_gap / 2.0)   # Calculate the boundary limit (e.g., 175 degrees if gap is 10)
        return abs(theta_cyl) > limit  # Using abs() catches both the positive and negative boundaries


def register(factory) -> None:
    """
    Register :class:`FileMeasurementPoints` with the given factory.

    :param factory: The factory used to register the measurement-points type.
    """
    factory.register("FileMeasurementPoints", FileMeasurementPoints)
