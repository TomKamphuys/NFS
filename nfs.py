from audio import Audio
from scanner import Scanner, ScannerFactory, CylindricalPosition
from loguru import logger
import configparser
from audio import Audio, AudioFactory


# logger.remove(0)
# logger.add('scanner.log', mode='w', level="TRACE")


class MeasurementPointsFactory:
    def create(self, config_file):
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)
        section = 'measurement_points'
        nr_of_angular_points = config_parser.getint(section, 'nr_of_angular_points')
        nr_of_radial_cap_points = config_parser.getint(section, 'nr_of_radial_cap_points')
        nr_of_vertical_points = config_parser.getint(section, 'nr_of_vertical_points')
        cap_spacing = config_parser.getint(section, 'cap_spacing')
        wall_spacing = config_parser.getint(section, 'wall_spacing')
        height = config_parser.getint(section, 'height')
        radius = config_parser.getint(section, 'radius')
        return MeasurementPoints(nr_of_angular_points,
                                 nr_of_radial_cap_points,
                                 nr_of_vertical_points,
                                 cap_spacing,
                                 wall_spacing,
                                 radius,
                                 height)


class MeasurementPoints:
    def __init__(self,
                 nr_of_angular_points,
                 nr_of_radial_cap_points,
                 nr_of_vertical_points,
                 cap_spacing,
                 wall_spacing,
                 radius,
                 height):
        self._nr_of_angular_points = nr_of_angular_points
        self._nr_of_radial_cap_points = nr_of_radial_cap_points
        self._nr_of_vertical_points = nr_of_vertical_points
        self._cap_spacing = cap_spacing
        self._wall_spacing = wall_spacing
        self._radius = radius
        self._height = height
        self._minimum_radius = 50
        self._evasive_move_needed = False
        self._ready = False
        self._current_angle = 0
        self._current_height = 0
        self._current_radius = self._minimum_radius
        self._bottom_cap = True
        self._wall = False
        self._top_cap = False

        self._delta_angle = 360.0 / self._nr_of_angular_points
        self._delta_height = self._height / self._nr_of_vertical_points
        self._delta_radius = (self._radius - self._minimum_radius) / self._nr_of_radial_cap_points

    def next(self):
        if self._bottom_cap:
            self._evasive_move_needed = False
            new_position = self.outwards_cap()

            # if last points of cap, set stuff for wall
            if new_position.r() >= self._radius:
                self._bottom_cap = False
                self._wall = True
                self._top_cap = False
            return new_position
        elif self._wall:
            self._evasive_move_needed = False
            new_position = self.wall()

            # if last position of wall, set stuff for cap
            if new_position.z() >= self._height:
                self._bottom_cap = False
                self._wall = False
                self._top_cap = True
                self._current_radius = self._minimum_radius
            return new_position
        elif self._top_cap:
            self._evasive_move_needed = False
            new_position = self.outwards_cap()

            # if last position of cap, get ready for new angle
            if new_position.r() >= self._radius:
                self._bottom_cap = True
                self._wall = False
                self._top_cap = False
                self._evasive_move_needed = True
                self._current_radius = self._minimum_radius
                self._current_height = 0
                self._current_angle += self._delta_angle

                # if this is the last angle, let them know
                if self._current_angle > 360:
                    self._ready = True
                return CylindricalPosition(self._current_radius, self._current_angle, self._current_height)
            return new_position
        else:
            logger.critical('This is not possible!')
            raise Exception("This is not possible!")

    def outwards_cap(self):
        self._current_radius += self._delta_radius
        return CylindricalPosition(
            self._current_radius,
            self._current_angle,
            self._current_height)

    def wall(self):
        self._current_height += self._delta_height
        return CylindricalPosition(
            self._current_radius,
            self._current_angle,
            self._current_height)

    def reset(self):
        pass

    def ready(self):
        return self._ready

    def need_to_do_evasive_move(self):
        return self._evasive_move_needed


class NearFieldScanner:
    def __init__(self, scanner, audio, measurement_points):
        self._scanner = scanner
        self._audio = audio
        self._measurement_points = measurement_points

    def take_single_measurement(self):
        self._audio.measure_ir(self._scanner.get_position())

    def take_measurement_set(self):
        while not self._measurement_points.ready():
            position = self._measurement_points.next()

            if self._measurement_points.need_to_do_evasive_move():
                self._scanner.evasive_move_to(position)
            else:
                self._scanner.move_to(position)
            self._audio.measure_ir(position)


class NearFieldScannerFactory:
    def create(self, config_file):
        scanner = ScannerFactory().create(config_file)
        audio = AudioFactory().create(config_file)
        measurement_points = MeasurementPointsFactory().create(config_file)

        return NearFieldScanner(scanner, audio, measurement_points)
