import math
import numpy as np
from nfs.datatypes import CylindricalPosition


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
