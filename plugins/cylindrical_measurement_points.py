from scanner import CylindricalPosition
from loguru import logger


class CylindricalMeasurementPoints:
    def __init__(self,
                 nr_of_angular_points,
                 nr_of_radial_cap_points,
                 nr_of_vertical_points,
                 cap_spacing,
                 wall_spacing,
                 radius,
                 height):
        self._nr_of_angular_points = int(nr_of_angular_points)
        self._nr_of_radial_cap_points = int(nr_of_radial_cap_points)
        self._nr_of_vertical_points = int(nr_of_vertical_points)
        self._cap_spacing = float(cap_spacing)
        self._wall_spacing = float(wall_spacing)
        self._radius = float(radius)
        self._height = float(height)
        self._minimum_radius = 50
        self._evasive_move_needed = False
        self._ready = False
        self._current_angle = -180  # No stitching errors where it matters most (0 degrees)
        self._current_height = 0
        self._current_radius = self._minimum_radius
        self._bottom_cap = True
        self._wall = False
        self._top_cap = False
        self._inner = False

        self._delta_angle = 360.0 / self._nr_of_angular_points
        self._delta_height = self._height / self._nr_of_vertical_points
        self._delta_radius = (self._radius - self._minimum_radius) / self._nr_of_radial_cap_points

    def next(self) -> CylindricalPosition:
        if self._bottom_cap:
            self._evasive_move_needed = False
            new_position = self.outwards_cap()

            # if last points of cap, set stuff for wall
            if new_position.r() >= self._radius:
                logger.info('Bottom cap ready; switching to wall')
                self._bottom_cap = False
                self._wall = True
                self._top_cap = False
            return new_position
        elif self._wall:
            self._evasive_move_needed = False
            new_position = self.wall()

            # if last position of wall, set stuff for top cap
            if new_position.z() >= self._height:
                logger.info('Wall ready, switching to top cap')
                self._bottom_cap = False
                self._wall = False
                self._top_cap = True
                self._current_radius = self._minimum_radius - self._delta_radius  # so we start at minimum radius
            return new_position
        elif self._top_cap:
            self._evasive_move_needed = False
            new_position = self.outwards_cap()

            # if last position of cap, get ready for new angle
            if new_position.r() >= self._radius:
                logger.info('Top cap ready, switching to new angle')
                self._bottom_cap = True
                self._wall = False
                self._top_cap = False
                self._evasive_move_needed = True
                self._current_radius = self._minimum_radius - self._delta_radius  # so we start at minimum radius
                self._current_height = 0
                self._current_angle += self._delta_angle
                self._inner = False

                # if this is the last angle, let them know
                if self._current_angle > 180 - self._delta_angle:
                    self._ready = True
                return CylindricalPosition(self._current_radius, self._current_angle, self._current_height)
            return new_position
        else:
            logger.critical('This is not possible!')
            raise Exception("This is not possible!")

    def outwards_cap(self) -> CylindricalPosition:
        if self._inner & self._bottom_cap:
            self._current_height -= self._cap_spacing  # only down
            self._inner = False
        elif (not self._inner) & self._bottom_cap:
            self._current_height += self._cap_spacing  # up and...
            self._current_radius += self._delta_radius  # out
            self._inner = True
        elif self._inner & self._top_cap:
            self._current_height += self._cap_spacing  # only up
            self._inner = False
        elif (not self._inner) & self._top_cap:
            self._current_height -= self._cap_spacing  # down and...
            self._current_radius += self._delta_radius  # out
            self._inner = True

        return CylindricalPosition(
            self._current_radius,
            self._current_angle,
            self._current_height)

    def wall(self) -> CylindricalPosition:
        if self._inner:
            self._current_radius += self._delta_radius  # Only out
            self._inner = False
        else:
            self._current_height += self._delta_height  # Up and...
            self._current_radius -= self._delta_radius  # in
            self._inner = True

        return CylindricalPosition(
            self._current_radius,
            self._current_angle,
            self._current_height)

    def reset(self) -> None:
        pass

    def ready(self) -> bool:
        return self._ready

    def need_to_do_evasive_move(self) -> bool:
        return self._evasive_move_needed


def register(factory) -> None:
    factory.register("CylindricalMeasurementPoints", CylindricalMeasurementPoints)
