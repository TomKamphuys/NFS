import csv

from loguru import logger

from nfs.datatypes import CylindricalPosition


class FileMeasurementPoints:
    """Base class for file measurement points plugins."""

    def __init__(self, filename: str,
                 homing_gap: float,
                 pole_gap: float):
        self._homing_gap = float(homing_gap)
        self._pole_gap = float(pole_gap)
        self._points: list[CylindricalPosition] = []
        self._current_index = 0
        self._ready = False

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
            print(f"Point {idx}: ({r_xy_mm:.2f}, {phi_deg:.2f}, {z_mm:.2f})")
            self._points.append(CylindricalPosition(r_xy_mm, phi_deg, z_mm))

        logger.info(f"Read {len(self._points)} points from input file '{filename}' (out of {len(coords)} rows in file)")

    def next(self) -> CylindricalPosition:
        if self._current_index < len(self._points):
            point = self._points[self._current_index]
            self._current_index += 1
            return point
        raise StopIteration("No more points")

    def reset(self) -> None:
        self._current_index = 0

    def ready(self) -> bool:
        return self._current_index > len(self._points) # Changed >= to > so final point runs a sweep

    def need_to_do_evasive_move(self) -> bool: # TODO MPOT I dont think this is used
        return False

    def _remove_point_inside_speaker_stand(self, r_cyl) -> bool:
        # everything in mm and degrees
        return r_cyl < (self._pole_gap / 2.0)

    def _remove_point_inside_homing_area(self, theta_cyl) -> bool:

        limit = 180.0 - (self._homing_gap / 2.0)   # Calculate the boundary limit (e.g., 175 degrees if gap is 10)
        return abs(theta_cyl) > limit # Using abs() catches both the positive and negative boundaries 

def register(factory) -> None:
    factory.register("FileMeasurementPoints", FileMeasurementPoints)