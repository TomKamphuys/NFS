from datatypes import CylindricalPosition
import numpy as np


class SphericalMeasurementPointsArcsRandom:
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
    factory.register("SphericalMeasurementPointsArcsRandom", SphericalMeasurementPointsArcsRandom)
