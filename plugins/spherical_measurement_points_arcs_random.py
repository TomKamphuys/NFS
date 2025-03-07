from datatypes import CylindricalPosition
import numpy as np


class SphericalMeasurementPointsArcsRandom:
    """
    Class designed to generate a set of random 3D spherical measurement points
    distributed across arcs, which are then transformed into cylindrical
    coordinates for further processing, while maintaining constraints like
    specific radius, wall spacing, and minimum radial distance.

    This class provides methods to access the calculated points sequentially, reset
    access to the beginning of the list, and verify if all points have been
    accessed. The algorithm ensures a uniform distribution of points over a sphere,
    followed by transformations and filtering to meet physical constraints.

    :ivar _ready: Flag indicating whether all points have been accessed.
    :type _ready: bool
    :ivar _radius: Desired radius of the spherical region from which random
        measurements are generated.
    :type _radius: float
    :ivar _wall_spacing: Spacing constraint for wall measurements.
    :type _wall_spacing: float
    :ivar _nr_of_points: Total number of spherical measurement points to generate.
    :type _nr_of_points: int
    :ivar _thetas: Array of polar angles of generated spherical coordinates.
    :type _thetas: numpy.ndarray
    :ivar _phis: Array of azimuthal angles of generated spherical coordinates.
    :type _phis: numpy.ndarray
    :ivar _r_cyl: Filtered radial distances in cylindrical coordinates.
    :type _r_cyl: numpy.ndarray
    :ivar _theta_cyl: Filtered angular positions in cylindrical coordinates, in
        degrees.
    :type _theta_cyl: numpy.ndarray
    :ivar _z_cyl: Filtered z-coordinates in cylindrical coordinates.
    :type _z_cyl: numpy.ndarray
    :ivar _actual_nr_of_points: Actual number of points after filtering for
        constraints.
    :type _actual_nr_of_points: int
    :ivar _current_index: Index tracking the next point to be retrieved.
    :type _current_index: int
    """
    def __init__(self,
                 nr_of_points,
                 wall_spacing,
                 radius):
        self._ready = False
        self._radius = float(radius)
        self._wall_spacing = float(wall_spacing)
        self._nr_of_points = int(nr_of_points)

        n = self._nr_of_points
        a = 4 * np.pi / n  # r ^ 2 = 1, 'a' is the surface area around a single point
        d = np.sqrt(a)  # this is the length of the (assumed) square area
        m_theta = round(np.pi / d)  # this is the amount of circles
        d_theta = np.pi / m_theta  # length resulting in an integer number of circles
        d_phi = a / d_theta  # other side of square (which has become a rectangle)

        self._thetas = np.empty((0, 0))
        self._phis = np.empty((0, 0))

        for m in range(m_theta):
            theta = np.pi * (m + 0.5) / m_theta  # theta of the circle
            m_phi = round(2 * np.pi * np.sin(theta) / d_phi)  # number of points on circle

            for n in range(m_phi):
                phi = 2 * np.pi * n / m_phi  # phi for every point
                self._thetas = np.append(self._thetas, theta)
                self._phis = np.append(self._phis, phi)

        radius = np.random.uniform(self._radius - self._wall_spacing, self._radius, np.size(self._phis))

        x = radius * np.sin(self._thetas) * np.cos(self._phis)
        y = radius * np.sin(self._thetas) * np.sin(self._phis)
        z = radius * np.cos(self._thetas)

        r_temp = np.sqrt(x ** 2 + y ** 2)
        theta_cyl_temp = np.arctan2(x, y) / np.pi * 180
        theta_cyl_temp = np.around(theta_cyl_temp, 2)

        sorted_indices = np.argsort(theta_cyl_temp * 100000 + z)

        r_cyl = r_temp[sorted_indices]
        theta_cyl = theta_cyl_temp[sorted_indices]
        z_cyl = z[sorted_indices]

        # everything in mm and degrees
        keep_indices = (r_cyl > 30.0)  # diameter central pole is 50mm
        self._r_cyl = r_cyl[keep_indices]
        self._theta_cyl = theta_cyl[keep_indices]
        self._z_cyl = z_cyl[keep_indices]

        self._actual_nr_of_points = self._r_cyl.size
        self._current_index = 0

    def get_radius(self) -> float:
        return self._radius

    def next(self) -> CylindricalPosition:
        i = self._current_index
        r = self._r_cyl[i]
        theta = self._theta_cyl[i]
        z = self._z_cyl[i]

        self._current_index += 1

        if self._current_index == self._actual_nr_of_points:
            self._ready = True

        return CylindricalPosition(r, theta, z)

    def reset(self) -> None:
        self._current_index = 0

    def ready(self) -> bool:
        return self._ready


def register(factory) -> None:
    """
    Registers a specific class or object with a given factory. This allows the
    provided factory instance to handle the creation or instantiation of the
    "SphericalMeasurementPointsArcsRandom" class, facilitating dynamic object
    creation and loose coupling between components.

    :param factory: The factory instance that will be used to register
        the "SphericalMeasurementPointsArcsRandom" class.
    :type factory: Any
    :return: None
    """
    factory.register("SphericalMeasurementPointsArcsRandom", SphericalMeasurementPointsArcsRandom)
