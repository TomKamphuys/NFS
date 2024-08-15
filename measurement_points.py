from typing import Protocol
from datatypes import CylindricalPosition


class MeasurementPoints(Protocol):
    def next(self) -> CylindricalPosition:
        pass

    def ready(self) -> bool:
        pass

    def need_to_do_evasive_move(self) -> bool:
        pass
