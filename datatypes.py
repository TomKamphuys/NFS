import math
import numpy as np

class CylindricalPosition:
    """
    Class for a position in a cylindrical coordinate system.
    """
    def __init__(self, r, t, z):
        self._r = r
        self._t = t
        self._z = z

    def __eq__(self, other) -> bool:
        """
        Equality method
        :param other: position
        :return: whether this and other is the equal
        """
        return (self.r(), self.t(), self.z()) == (other.r(), other.t(), other.z())

    def __str__(self) -> str:
        return f'({self.r()}, {self.t()}, {self.z()})'

    def r(self) -> float:
        """
        :return: the radius of the position in cylindrical coordinates
        """
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
        """
        :return: the distance of the point from the origin
        """
        return math.sqrt(self.r() ** 2 + self.z() ** 2)


def cyl_to_cart(cylindrical_position: CylindricalPosition):
    """
    Coordinate transformation from cylindrical coordinates to cartesian coordinates
    :param cylindrical_position:
    :return: the position in cartesian coordinates
    """
    r = cylindrical_position.r()
    t = cylindrical_position.t() / 180 * np.pi
    z = cylindrical_position.z()

    x = r * np.cos(t)
    y = r * np.sin(t)
    # z = z

    return x, y, z
