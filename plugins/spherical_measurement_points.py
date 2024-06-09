from scanner import CylindricalPosition
from loguru import logger
import numpy as np


class SphericalMeasurementPoints:
    def __init__(self,
                 nr_of_points,
                 wall_spacing,
                 radius):
        self._ready = False
        self._evasive_move_needed = False
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

        self._go_to_next_circle()  # start with first circle

    def next(self) -> CylindricalPosition:
        self._go_to_next_point()

        x = self._radius * np.sin(self._theta) * np.cos(self._phi)
        y = self._radius * np.sin(self._theta) * np.sin(self._phi)
        z = self._radius * np.cos(self._theta)

        r = np.sqrt(x**2 + y**2)
        theta = np.arctan2(x, y)

        return CylindricalPosition(r, theta/np.pi*180, z + self._radius)  # zero is at bottom of sphere

    def _go_to_next_circle(self):
        self._theta = np.pi * (self._m + 0.5) / self._m_theta  # theta of the circle
        self._m_phi = round(2 * np.pi * np.sin(self._theta) / self._d_phi)  # number of points on circle

        self._m += 1
        if self._m == self._m_theta - 1:
            self._last_circle = True

    def _go_to_next_point(self):
        if self._n == self._m_phi:
            if self._last_circle:
                self._ready = True
            self._n = 0
            self._go_to_next_circle()

        self._phi = 2 * np.pi * self._n / self._m_phi  # phi for every point
        self._n += 1

    def reset(self) -> None:
        pass

    def ready(self) -> bool:
        return self._ready

    def need_to_do_evasive_move(self) -> bool:
        return self._evasive_move_needed


def register(factory) -> None:
    factory.register("SphericalMeasurementPoints", SphericalMeasurementPoints)
