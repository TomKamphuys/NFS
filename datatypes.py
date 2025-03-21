import math

import numpy as np


class GrblConfig:
    """
    Represents the configuration for a GRBL stepper motor controller.

    This class encapsulates various settings required to configure a stepper motor controller
    operating with GRBL firmware. Settings include steps per millimeter, maximum rate,
    acceleration, and whether the direction is inverted.

    :ivar _steps_per_millimeter: Defines the number of steps required for the motor to move
        one millimeter.
    :type _steps_per_millimeter: float
    :ivar _maximum_rate: Specifies the maximum allowable movement rate (in units per minute).
    :type _maximum_rate: float
    :ivar _acceleration: Configures the acceleration of the motor (in units per second
        squared).
    :type _acceleration: float
    :ivar _invert_direction: Indicates whether the direction of the motor is inverted.
    :type _invert_direction: bool
    """
    def __init__(self, steps_per_millimeter: float, maximum_rate: float, acceleration: float, invert_direction: bool):
        self._steps_per_millimeter = steps_per_millimeter
        self._maximum_rate = maximum_rate
        self._acceleration = acceleration
        self._invert_direction = invert_direction

    @property
    def steps_per_millimeter(self) -> float:
        return self._steps_per_millimeter

    @property
    def maximum_rate(self) -> float:
        return self._maximum_rate

    @property
    def acceleration(self) -> float:
        return self._acceleration

    @property
    def invert_direction(self) -> bool:
        return self._invert_direction


class CylindricalPosition:
    """
    Represents a position in cylindrical coordinates.

    This class encapsulates a point in cylindrical coordinates defined by radius (r),
    theta (t, angular displacement), and height (z). It provides methods for accessing
    and modifying its coordinates, as well as calculating the length of the point from
    the origin.

    :ivar _r: The radial distance from the origin.
    :type _r: float
    :ivar _t: The angular coordinate in radians.
    :type _t: float
    :ivar _z: The height coordinate in cylindrical space.
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

    This function takes an object `cylindrical_position`, which represents a
    point in cylindrical coordinate space, and converts it to its corresponding
    Cartesian coordinate representation. It uses the radius `r`, azimuthal angle
    `t` (in degrees, converted to radians internally), and height `z` to compute
    the x, y, and z positions in Cartesian space.

    :param cylindrical_position: A CylindricalPosition object containing the
        radius (r), angle (t, in degrees), and height (z).
    :return: A tuple containing the Cartesian coordinates (x, y, z).
    :rtype: tuple[float, float, float]
    """
    r = cylindrical_position.r()
    t = cylindrical_position.t() / 180.0 * np.pi
    z = cylindrical_position.z()

    x = r * np.cos(t)
    y = r * np.sin(t)
    # z = z

    return x, y, z
