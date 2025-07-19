from typing import Protocol

from datatypes import CylindricalPosition


class MeasurementPoints(Protocol):
    """
    This class defines the protocol for measurement points in a system, ensuring a specific interface
    is followed. It provides functionality to retrieve measurement positions in cylindrical coordinates,
    check the readiness state of the points, get a specified radius, reset internal states, and determine
    if evasive maneuvers are necessary. It is typically used in applications involving positional
    data collection or path planning.
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
