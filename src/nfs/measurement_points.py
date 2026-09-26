from typing import Protocol

from .datatypes import CylindricalPosition


class MeasurementPoints(Protocol):
    """
    This class defines the protocol for measurement points in a system, ensuring a specific interface
    is followed. It provides functionality to retrieve measurement positions in cylindrical coordinates,
    check the readiness state of the points, reset internal state, and report the total number of
    points. Collision avoidance (no-fly zones and evasive moves) is the responsibility of the motion
    managers, not of the measurement points.
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

    def reset(self) -> None:
        """
        Rewind the sequence so iteration starts again from the first point.
        """
        pass

    def total_points(self) -> int:
        """
        Return the total number of measurement points in the set.

        :return: The total number of points.
        :rtype: int
        """
        pass
