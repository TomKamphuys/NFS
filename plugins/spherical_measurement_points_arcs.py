from scanner import CylindricalPosition
import numpy as np


class SphericalMeasurementPointsArcs:
    def __init__(self,
                 nr_of_points,
                 wall_spacing,
                 radius):
        self._ready = False
        self._radius = float(radius)
        self._wall_spacing = float(wall_spacing)
        self._nr_of_points = int(nr_of_points)

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

        r_temp = np.sqrt(x ** 2 + y ** 2)
        theta_cyl_temp = np.arctan2(x, y) / np.pi * 180

        # r_temp = np.around(r_temp, 2)
        theta_cyl_temp = np.around(theta_cyl_temp, 2)
        # z = np.around(z, 2)

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
    factory.register("SphericalMeasurementPointsArcs", SphericalMeasurementPointsArcs)
