import numpy as np

from datatypes import CylindricalPosition


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
                 nr_of_points: int,
                 wall_spacing: float,
                 radius: float) -> None:

        self._ready = False
        self._radius = radius
        self._wall_spacing = wall_spacing
        self._nr_of_points = nr_of_points

        self._generate_evenly_spread_points_on_unit_sphere()

        radius = np.random.uniform(self._radius - self._wall_spacing, self._radius, np.size(self._phis))

        x, y, z = self._spherical_to_cartesian(radius)

        r_cyl, theta_cyl, z_cyl = self._convert_to_sorted_cylindrical_coordinates(x, y, z)

        self._remove_points_inside_speaker_stand(r_cyl, theta_cyl, z_cyl)

        self._actual_nr_of_points = self._r_cyl.size
        self._current_index = 0

    def _convert_to_sorted_cylindrical_coordinates(self, x, y, z):
        r_temp = np.sqrt(x ** 2 + y ** 2)
        theta_cyl_temp = np.arctan2(x, y) / np.pi * 180
        theta_cyl_temp = np.around(theta_cyl_temp, 0)
        sorted_indices = np.argsort(theta_cyl_temp * 100000 + z)
        r_cyl = r_temp[sorted_indices]
        theta_cyl = theta_cyl_temp[sorted_indices]
        z_cyl = z[sorted_indices]
        return r_cyl, theta_cyl, z_cyl

    def _remove_points_inside_speaker_stand(self, r_cyl, theta_cyl, z_cyl):
        # everything in mm and degrees
        keep_indices = (r_cyl > 30.0)  # diameter central pole is 50mm
        self._r_cyl = r_cyl[keep_indices]
        self._theta_cyl = theta_cyl[keep_indices]
        self._z_cyl = z_cyl[keep_indices]

    def _spherical_to_cartesian(self, radius):
        x = radius * np.sin(self._thetas) * np.cos(self._phis)
        y = radius * np.sin(self._thetas) * np.sin(self._phis)
        z = radius * np.cos(self._thetas)
        return x, y, z

    def _generate_evenly_spread_points_on_unit_sphere(self):
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

    def get_radius(self) -> float:
        """
        Represents a function to retrieve the radius of a given object.

        This method is designed to return the private attribute `_radius`, which stores
        the radius value. It does not modify any attribute or take any parameters, and
        it returns the radius as a float.

        :return: The radius of the object.
        :rtype: float
        """
        return self._radius

    def next(self) -> CylindricalPosition:
        """
        Retrieve the next cylindrical position from the internal lists of cylindrical
        coordinates. The method iterates through a pre-defined set of cylindrical
        positions. Once the end of the list is reached, the ready status is updated.
        :raises IndexError: If the lists are accessed out of bounds due to incorrect
            indexing or inconsistent list sizes.
        :return: A `CylindricalPosition` object representing the current cylindrical
            coordinates.
        :rtype: CylindricalPosition
        """
        # Extract current cylindrical data as a tuple
        current_coords = (self._r_cyl[self._current_index],
                          self._theta_cyl[self._current_index],
                          self._z_cyl[self._current_index])

        self._current_index += 1
        self._check_ready_status()  # Update ready flag if iteration is complete

        # Create the CylindricalPosition object and return
        return CylindricalPosition(*current_coords)

    def _check_ready_status(self) -> None:
        """Update the ready status when iteration is complete."""
        self._ready = self._current_index >= self._actual_nr_of_points

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
