import math

class CylindricalPosition:
    def __init__(self, r, t, z):
        self._r = r
        self._t = t
        self._z = z

    def __eq__(self, other) -> bool:
        return (self.r(), self.t(), self.z()) == (other.r(), other.t(), other.z())

    def __str__(self) -> str:
        return f'({self.r()}, {self.t()}, {self.z()})'

    def r(self) -> float:
        return self._r

    def set_r(self, r: float) -> None:
        self._r = r

    def t(self) -> float:
        return self._t

    def set_t(self, t: float) -> None:
        self._t = t

    def z(self) -> float:
        return self._z

    def set_z(self, z: float) -> None:
        self._z = z

    def length(self) -> float:
        return math.sqrt(self.r() ** 2 + self.z() ** 2)