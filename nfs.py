from scanner import ScannerFactory
import configparser
from audio import AudioFactory
import factory
import loader


# # Define plane
# planeNormal = np.array([0, 0, 1])
# planePoint = np.array([0, 0, 5])  # Any point on the plane
#
# # Define ray
# rayDirection = np.array([0, -1, -1])
# rayPoint = np.array([0, 0, 10])  # Any point along the ray
#
# Psi = LinePlaneCollision(planeNormal, planePoint, rayDirection, rayPoint)


def has_intersect(plane_normal, ray_direction, epsilon=1e-6):
    n_dot_u = plane_normal.dot(ray_direction)
    if abs(n_dot_u) < epsilon:
        return False

    return True


def line_plane_intersection(plane_normal, plane_point, ray_direction, ray_point):
    n_dot_u = plane_normal.dot(ray_direction)

    w = ray_point - plane_point
    si = -plane_normal.dot(w) / n_dot_u
    psi = w + si * ray_direction + plane_point
    return psi


class NearFieldScanner:
    def __init__(self, scanner, audio, measurement_points):
        self._scanner = scanner
        self._audio = audio
        self._measurement_points = measurement_points

    def take_single_measurement(self) -> None:
        self._audio.measure_ir(self._scanner.get_position())

    def take_measurement_set(self) -> None:
        while not self._measurement_points.ready():
            position = self._measurement_points.next()
            if self._measurement_points.ready():
                break

            if self._measurement_points.need_to_do_evasive_move():
                self._scanner.evasive_move_to(position)
            else:
                self._scanner.move_to(position)

            self._audio.measure_ir(position)

    def shutdown(self) -> None:
        pass  # turn off stuff and tidy


class NearFieldScannerFactory:
    @staticmethod
    def create(config_file: str) -> NearFieldScanner:
        scanner = ScannerFactory().create(config_file)
        audio = AudioFactory().create(config_file)

        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        items = config_parser.items('plugins')
        _, plugins = zip(*items)

        # load the plugins
        loader.load_plugins(plugins)

        item = dict(config_parser.items('measurement_points'))
        measurement_points = factory.create(item)

        return NearFieldScanner(scanner, audio, measurement_points)
