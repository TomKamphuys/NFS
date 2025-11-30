import csv

from nfs.datatypes import CylindricalPosition
import numpy as np


class FileMeasurementPoints:
    """Base class for file measurement points plugins."""

    def __init__(self, filename: str):
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
            self._points.append(CylindricalPosition(r_xy_mm*0.9, (phi_deg-180)*0.9, (z_mm-400)/1.5)) # TODO MPOT some magic to limit the motion to stay clear of end stops

    def next(self) -> CylindricalPosition:
        if self._current_index < len(self._points):
            point = self._points[self._current_index]
            self._current_index += 1
            return point
        raise StopIteration("No more points")

    def reset(self) -> None:
        self._current_index = 0

    def ready(self) -> bool:
        return self._current_index >= len(self._points)

    def need_to_do_evasive_move(self) -> bool: # TODO MPOT I dont think this is used
        return False

def register(factory) -> None:
    factory.register("FileMeasurementPoints", FileMeasurementPoints)