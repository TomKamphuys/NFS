from typing import Protocol
from datatypes import CylindricalPosition


class MeasurementPoints(Protocol):
    """
    Interface for managing and acquiring measurement points.

    This protocol defines the structure for interacting with measurement
    points in a system. It provides methods to retrieve the next measurement
    point, check if the system is ready for measurement, and determine if
    an evasive move is required. Implementing classes should provide the
    specific behavior for these operations.
    """
    def next(self) -> CylindricalPosition:
        pass

    def ready(self) -> bool:
        pass

    def get_radius(self) -> float:
        pass

    def reset(self) -> None:
        pass

    def need_to_do_evasive_move(self) -> bool:
        pass
