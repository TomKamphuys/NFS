from typing import Protocol

from .datatypes import CylindricalPosition


class MeasurementPoints(Protocol):
    """
    This class defines the protocol for measurement points in a system, ensuring a specific interface
    is followed. It provides functionality to retrieve measurement positions in cylindrical coordinates,
    check the readiness state of the points, get a specified radius, reset internal states, and determine
    if evasive maneuvers are necessary. It is typically used in applications involving positional
    data collection or path planning.
    """
    def next(self) -> CylindricalPosition:
        """
        Advance the internal cursor and return the next measurement position.

        :return: The next measurement point in cylindrical coordinates.
        :rtype: CylindricalPosition
        """
        pass

    def ready(self) -> bool:
        """
        Report whether every measurement point has been served.

        :return: True once the sequence is exhausted, False otherwise.
        :rtype: bool
        """
        pass

    def get_radius(self) -> float:
        """
        Return a nominal radius (mm) associated with the point set.

        :return: The nominal radius in millimeters.
        :rtype: float
        """
        pass

    def reset(self) -> None:
        """
        Rewind the sequence so iteration starts again from the first point.
        """
        pass

    def need_to_do_evasive_move(self) -> bool:
        """
        Report whether reaching the point last returned by :meth:`next`
        requires a safe evasive maneuver around the protected interior.

        :return: True when an evasive move is required, False otherwise.
        :rtype: bool
        """
        pass

    def total_points(self) -> int:
        """
        Return the total number of measurement points in the set.

        :return: The total number of points.
        :rtype: int
        """
        pass
