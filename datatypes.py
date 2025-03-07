import math
import numpy as np

class CylindricalPosition:
    """
    Represents a position in 3D space defined by cylindrical coordinates.

    This class provides attributes and methods to manage and operate on a
    coordinate position specified in a cylindrical coordinate system (r, t, z),
    where `r` is the radial distance, `t` is the azimuthal angle, and `z` is the
    height. It includes functionality to compare positions, calculate the length
    from the origin, and access or modify individual coordinate components.

    :ivar _r: The radial distance from the z-axis.
    :type _r: float
    :ivar _t: The azimuthal angle relative to the x-axis, in radians.
    :type _t: float
    :ivar _z: The height component along the z-axis.
    :type _z: float
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
    Converts a cylindrical coordinate position to Cartesian coordinates.

    This function takes a `CylindricalPosition` object, extracts the values for
    radius, angle, and height (z-coordinate), converts the angle from degrees
    to radians, and computes the corresponding Cartesian coordinates (x, y, z).

    :param cylindrical_position: A `CylindricalPosition` object containing the
        cylindrical coordinates with attributes `r` (radius), `t` (angle in
        degrees), and `z` (height).
    :type cylindrical_position: CylindricalPosition
    :return: A tuple containing the Cartesian coordinates as (x, y, z).
    :rtype: tuple[float, float, float]
    """
    r = cylindrical_position.r()
    t = cylindrical_position.t() / 180 * np.pi
    z = cylindrical_position.z()

    x = r * np.cos(t)
    y = r * np.sin(t)
    # z = z

    return x, y, z
