from datatypes import CylindricalPosition
import numpy as np


class SphericalMeasurementPointsSorted:
    """
    Represents a spherical measurement point system, where coordinates are
    calculated and organized to accommodate the measurement of sound
    distribution from a speaker. Ensures that measurement points do not
    intrude into the physical volume of the speaker and organizes these
    points in cylindrical coordinates for iteration.

    The class calculates spherical measurement points, validates their
    position relative to a defined speaker's dimensions, and provides an
    interface for iterating through these points in a structured manner.


    :ivar _ready: True if all points have been iterated, False otherwise.
    :type _ready: bool
    :ivar _evasive_move_needed: True if a measurement point adjustment has
        been flagged as needed, False otherwise.
    :type _evasive_move_needed: bool
    :ivar _radius: The radius of the sphere where measurement points will
        be generated.
    :type _radius: float
    :ivar _wall_spacing: The spacing between the measurement sphere and
        inner boundary.
    :type _wall_spacing: float
    :ivar _nr_of_points: The number of measurement points to calculate on
        the sphere.
    :type _nr_of_points: int
    :ivar _speaker_height: The height of the speaker.
    :type _speaker_height: float
    :ivar _speaker_width: The width of the speaker.
    :type _speaker_width: float
    :ivar _speaker_depth: The depth of the speaker.
    :type _speaker_depth: float
    :ivar _thetas: Array containing theta coordinate values for measurement
        points.
    :type _thetas: numpy.ndarray
    :ivar _phis: Array containing phi coordinate values for measurement
        points.
    :type _phis: numpy.ndarray
    :ivar _r_cyl: Array containing radial distances in cylindrical
        coordinates.
    :type _r_cyl: numpy.ndarray
    :ivar _theta_cyl: Array containing angular positions in degrees for
        cylindrical coordinates.
    :type _theta_cyl: numpy.ndarray
    :ivar _z_cyl: Array containing z-coordinate values for cylindrical
        coordinates.
    :type _z_cyl: numpy.ndarray
    :ivar _actual_nr_of_points: The total number of valid measurement
        points after validation.
    :type _actual_nr_of_points: int
    :ivar _current_index: The current index of the measurement point in
        iteration.
    :type _current_index: int
    """
    def __init__(self,
                 nr_of_points,
                 wall_spacing,
                 radius,
                 speaker_height,
                 speaker_width,
                 speaker_depth):
        self._ready = False
        self._evasive_move_needed = False
        self._radius = float(radius)
        self._wall_spacing = float(wall_spacing)
        self._nr_of_points = int(nr_of_points)
        self._speaker_height = float(speaker_height)
        self._speaker_width = float(speaker_width)
        self._speaker_depth = float(speaker_depth)

        n = self._nr_of_points / 2
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

        x = self._radius * np.sin(self._thetas) * np.cos(self._phis)
        y = self._radius * np.sin(self._thetas) * np.sin(self._phis)
        z = self._radius * np.cos(self._thetas)

        inner_radius = self._radius - self._wall_spacing

        x_inner = inner_radius * np.sin(self._thetas) * np.cos(self._phis)
        y_inner = inner_radius * np.sin(self._thetas) * np.sin(self._phis)
        z_inner = inner_radius * np.cos(self._thetas)

        x = np.append(x, x_inner)
        y = np.append(y, y_inner)
        z = np.append(z, z_inner)

        # Check whether any points are inside the speaker volume
        bound_x = np.abs(x) < self._speaker_depth / 2
        bound_y = np.abs(y) < self._speaker_width / 2
        bound_z = np.abs(z) < self._speaker_height / 2

        bb_filter = np.logical_and(np.logical_and(bound_x, bound_y), bound_z)
        if np.sum(bb_filter) != 0:
            raise Exception(f'{np.sum(bb_filter)} points inside speaker volume.')

        r_temp = np.sqrt(x ** 2 + y ** 2)
        theta_cyl_temp = np.arctan2(x, y) / np.pi * 180

        r_temp = np.around(r_temp, 2)
        theta_cyl_temp = np.around(theta_cyl_temp, 2)
        z = np.around(z, 2)

        sorted_indices = np.argsort(theta_cyl_temp * 100000 + z)

        self._r_cyl = r_temp[sorted_indices]
        self._theta_cyl = theta_cyl_temp[sorted_indices]
        self._z_cyl = z[sorted_indices]

        self._actual_nr_of_points = self._r_cyl.size
        self._current_index = 0

    def next(self) -> CylindricalPosition:
        i = self._current_index
        r = self._r_cyl[i]
        theta = self._theta_cyl[i]
        z = self._z_cyl[i]  # TODO + self._radius  # zero is at bottom of sphere

        self._current_index += 1

        if self._current_index == self._actual_nr_of_points:
            self._ready = True

        return CylindricalPosition(r, theta, z)

    def reset(self) -> None:
        self._current_index = 0;

    def ready(self) -> bool:
        return self._ready

    def need_to_do_evasive_move(self) -> bool:
        return self._evasive_move_needed


def register(factory) -> None:
    """
    Registers a specific factory type with an associated class.

    This function binds the provided factory to the
    "SphericalMeasurementPointsSorted" configuration by associating it
    with the `SphericalMeasurementPointsSorted` class. This enables the
    factory to instantiate or work with the specified class through the
    established registration.

    :param factory: The factory object that requires registration of the
        "SphericalMeasurementPointsSorted" class.
    :type factory: Any
    :return: This function does not return any value.
    :rtype: None
    """
    factory.register("SphericalMeasurementPointsSorted", SphericalMeasurementPointsSorted)
