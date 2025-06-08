import math

import numpy as np


class GrblConfig:
    """
    Represents the configuration settings for a GRBL-based CNC machine.

    This class provides a representation for the key configuration attributes
    of a GRBL-based CNC machine, such as steps per millimeter, maximum movement
    rate, acceleration, and axis direction inversion. It allows for accessing
    these configuration parameters through properties.

    :ivar _steps_per_millimeter: The number of steps the motor must take to
        move 1 millimeter.
    :type _steps_per_millimeter: float
    :ivar _maximum_rate: The maximum speed at which the machine can move
        in units per minute.
    :type _maximum_rate: float
    :ivar _acceleration: The rate of increase of velocity, defining
        how quickly the machine can accelerate.
    :type _acceleration: float
    :ivar _invert_direction: A flag indicating whether the direction of
        movement should be inverted.
    :type _invert_direction: bool
    """
    def __init__(self, steps_per_millimeter: float, maximum_rate: float, acceleration: float, invert_direction: bool):
        self._steps_per_millimeter = steps_per_millimeter
        self._maximum_rate = maximum_rate
        self._acceleration = acceleration
        self._invert_direction = invert_direction

    @property
    def steps_per_millimeter(self) -> float:
        """
        Gets the number of steps per millimeter.

        This property retrieves the value of steps per millimeter,
        which is an important parameter for determining how many
        steps the motor takes for every millimeter of motion.

        :return: The number of steps per millimeter.
        :rtype: float
        """
        return self._steps_per_millimeter

    @property
    def maximum_rate(self) -> float:
        """
        Gets the maximum rate of a specific property.

        The `maximum_rate` property provides the value of the maximum rate
        for a particular context or operation. It retrieves the value
        stored in the private attribute `_maximum_rate`.

        :rtype: float
        :return: The value of the maximum rate.
        """
        return self._maximum_rate

    @property
    def acceleration(self) -> float:
        """
        Provides the acceleration of an object. This property retrieves the private attribute
        ``_acceleration`` that holds the value representing the rate of change of velocity of the
        object over time.

        :return: The value of the object's acceleration.
        :rtype: float
        """
        return self._acceleration

    @property
    def invert_direction(self) -> bool:
        """
        A property that retrieves the value of the `_invert_direction` attribute.

        This property indicates whether the direction is inverted or not. It allows
        external access to the state of `_invert_direction` in a read-only manner.

        :return: Current state of the `_invert_direction` attribute
        :rtype: bool
        """
        return self._invert_direction


class CylindricalPosition:
    """
    Represents a 3D position in cylindrical coordinates.

    This class encapsulates a cylindrical coordinate system position defined by radius
    (r), angle (t, in radians), and height (z). It provides methods to access and
    manipulate these coordinates, as well as to compare positions and compute derived
    properties like the length (distance from the origin in the r-z plane).

    :ivar _r: The radius of the position in cylindrical coordinates.
    :type _r: float
    :ivar _t: The angular coordinate.
    :type _t: float
    :ivar _z: The height of the position in cylindrical coordinates.
    :type _z: float
    """
    def __init__(self, r, t, z) -> None:
        self._r = r
        self._t = t
        self._z = z

    def __eq__(self, other) -> bool:
        """
        Compares the current object with another object for equality. The comparison
        is performed by comparing the results of `r()`, `t()`, and `z()` methods from
        both the current object and the provided `other` object.

        :param other: The object to compare with. It should support `r()`, `t()`,
                      and `z()` methods.
        :return: A boolean indicating whether the current object is equal to the
                 provided `other` object based on the comparison of `r()`, `t()`,
                 and `z()` method results.
        """
        return (self.r(), self.t(), self.z()) == (other.r(), other.t(), other.z())

    def __str__(self) -> str:
        """
        Converts the current object to its string representation.

        This method generates a string representation of the object, typically
        used for debugging or displaying its state in a human-readable format.
        It relies on calling other methods r(), t(), and z() within the object
        to construct the string output.

        :return: A string representation of the object in the format
                 "(self.r(), self.t(), self.z())".
        :rtype: str
        """
        return f'({self.r()}, {self.t()}, {self.z()})'

    def r(self) -> float:
        """
        Provides a method to return the value of the internal attribute `_r`.

        This method should be used when a numerical value representing `_r` needs
        to be accessed externally for computation or evaluation purposes.

        :return: The value of the internal attribute `_r`.
        :rtype: float
        """
        return self._r

    def set_r(self, r: float) -> None:
        """
        Sets the value of the attribute ``_r``.

        :param r: The new value to set for the ``_r`` attribute.
        :type r: float
        :return: None
        """
        self._r = r

    def t(self) -> float:
        """
        Retrieves a float value stored in the attribute `_t`.

        :rtype: float
        :return: The float value of the `_t` attribute.
        """
        return self._t

    def set_t(self, t: float) -> None:
        """
        Sets the value of the private attribute `_t`.

        The method updates the internal state of the instance by assigning the
        given value to the `_t` attribute.

        :param t: The new value to assign to the `_t` attribute.
        :type t: float
        :return: None
        """
        self._t = t

    def z(self) -> float:
        """
        Returns the value of the private attribute `_z`.

        This method retrieves and returns the value stored in the private
        attribute `_z`. It provides access to the encapsulated data in a
        controlled manner.

        :return: The value of the `_z` attribute
        :rtype: float
        """
        return self._z

    def set_z(self, z: float) -> None:
        """
        Updates the value of the instance attribute `_z`.

        The method sets the provided value to the `_z` attribute, which represents a
        floating-point value associated with the instance. After calling this method,
        the `_z` attribute will hold the new value provided as input.

        :param z: The new floating-point value to set for the `_z` attribute.
        :return: None
        """
        self._z = z

    def length(self) -> float:
        """
        Calculates the length of a vector in a 2D space defined by its r and z components.

        The method computes the length using the Euclidean distance formula, which is
        the square root of the sum of the squares of the r and z components. It assumes
        that the r() and z() methods return the respective components of the vector.

        :return: The length of the vector in 2D space.
        :rtype: float
        """
        return math.sqrt(self.r() ** 2 + self.z() ** 2)


def cyl_to_cart(cylindrical_position: CylindricalPosition) -> tuple[float, float, float]:
    """
    Convert a cylindrical coordinate to a cartesian coordinate.

    This function takes a `CylindricalPosition` object as input, extracts its
    radial distance (r), angular displacement (theta, in degrees), and z-coordinate,
    and converts these cylindrical coordinates into cartesian coordinates
    (x, y, z). The conversion is performed using trigonometric calculations for x
    and y from r and theta.

    :param cylindrical_position: The input cylindrical position object containing
                                 the radial distance, the angular displacement
                                 in degrees, and the z-coordinate.
    :type cylindrical_position: CylindricalPosition
    :return: A tuple representing the cartesian coordinates (x, y, z).
    :rtype: tuple[float, float, float]
    """
    r = cylindrical_position.r()
    t = math.radians(cylindrical_position.t())
    z = cylindrical_position.z()

    x = r * np.cos(t)
    y = r * np.sin(t)
    return x, y, z
