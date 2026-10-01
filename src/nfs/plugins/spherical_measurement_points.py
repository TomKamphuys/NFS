import numpy as np

from nfs.datatypes import CylindricalPosition


class SphericalMeasurementPoints:
    """
    Generates roughly evenly spread measurement points on a sphere.

    Points are laid out circle by circle from pole to pole (the classic
    "equal-area" spiral distribution) and yielded lazily in cylindrical
    coordinates, with the origin shifted so that zero height sits at the bottom
    of the sphere.
    """

    def __init__(self,
                 nr_of_points,
                 wall_spacing,
                 radius):
        """
        Initialize the spherical measurement-point generator.

        :param nr_of_points: Target number of points to distribute on the sphere.
        :param wall_spacing: Radial wall spacing (mm) of the measurement shell.
        :param radius: Radius (mm) of the measurement sphere.
        """
        self._ready = False
        self._radius = float(radius)
        self._wall_spacing = float(wall_spacing)
        self._nr_of_points = int(nr_of_points)

        self._last_circle = False

        n = self._nr_of_points
        n_count = 1
        a = 4 * np.pi / n  # r ^ 2 = 1, a is the surface area around a single point
        d = np.sqrt(a)  # this is the length of the (assumed) square area
        self._m_theta = round(np.pi / d)  # this is the amount of circles
        d_theta = np.pi / self._m_theta # length resulting in an integer number of circles
        self._d_phi = a / d_theta  # other side of square (which has become a rectangle)
        self._m = 0
        self._n = 0
        self._phi = 0
        self._theta = 0
        self._m_phi = 0

        self._actual_nr_of_points = 0
        m_temp = 0
        while m_temp < self._m_theta:
            theta_temp = np.pi * (m_temp + 0.5) / self._m_theta
            m_phi_temp = round(2 * np.pi * np.sin(theta_temp) / self._d_phi)
            if m_phi_temp > 0:
                self._actual_nr_of_points += m_phi_temp
            m_temp += 1

        self._go_to_next_circle()  # start with first circle

    def next(self) -> CylindricalPosition:
        """
        Advance to and return the next point on the sphere.

        :return: The next measurement point in cylindrical coordinates.
        :rtype: CylindricalPosition
        """
        self._go_to_next_point()

        x = self._radius * np.sin(self._theta) * np.cos(self._phi)
        y = self._radius * np.sin(self._theta) * np.sin(self._phi)
        z = self._radius * np.cos(self._theta)

        r = np.sqrt(x**2 + y**2)
        theta = np.arctan2(x, y)

        return CylindricalPosition(r, theta/np.pi*180, z + self._radius)  # zero is at bottom of sphere

    def _go_to_next_circle(self):
        """
        Advance the internal state to the next latitude circle of the sphere.
        """
        self._theta = np.pi * (self._m + 0.5) / self._m_theta  # theta of the circle
        self._m_phi = round(2 * np.pi * np.sin(self._theta) / self._d_phi)  # number of points on circle

        self._m += 1
        if self._m == self._m_theta - 1:
            self._last_circle = True

    def _go_to_next_point(self):
        """
        Advance to the next point on the current circle, wrapping to the next
        circle when the current one is exhausted.
        """
        if self._n == self._m_phi:
            if self._last_circle:
                self._ready = True
            self._n = 0
            self._go_to_next_circle()

        self._phi = 2 * np.pi * self._n / self._m_phi  # phi for every point
        self._n += 1

    def reset(self) -> None:
        """
        Reset the generator (no-op for this generator).
        """
        pass

    def ready(self) -> bool:
        """
        Report whether all points have been generated.

        :return: True once the sequence is exhausted, False otherwise.
        :rtype: bool
        """
        return self._ready

    def total_points(self) -> int:
        """
        Return the actual number of points distributed on the sphere.

        :return: The total number of measurement points.
        :rtype: int
        """
        return self._actual_nr_of_points


def register(factory) -> None:
    """
    Register :class:`SphericalMeasurementPoints` with the given factory.

    :param factory: The factory used to register the measurement-points type.
    """
    factory.register("SphericalMeasurementPoints", SphericalMeasurementPoints)
