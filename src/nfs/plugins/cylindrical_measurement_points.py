from loguru import logger

from nfs.datatypes import CylindricalPosition


class CylindricalMeasurementPoints:
    """
    Generates measurement points on the surface of a cylinder.

    Points are produced angular sector by angular sector. Within each sector the
    path sweeps the bottom cap outwards, climbs the wall, and finally sweeps the
    top cap outwards, using a zigzag pattern so that consecutive points stay on
    the same measurement surface. When a sector is finished the generator flags
    an evasive move for the transition to the next sector's bottom cap.
    """

    def __init__(self,
                 nr_of_angular_points,
                 nr_of_radial_cap_points,
                 nr_of_vertical_points,
                 cap_spacing,
                 wall_spacing,
                 radius,
                 height):
        """
        Initialize the cylindrical measurement-point generator.

        :param nr_of_angular_points: Number of angular sectors around the cylinder.
        :param nr_of_radial_cap_points: Number of radial steps across each cap.
        :param nr_of_vertical_points: Number of vertical steps along the wall.
        :param cap_spacing: Vertical zigzag spacing (mm) used on the caps.
        :param wall_spacing: Radial zigzag spacing (mm) used on the wall.
        :param radius: Outer radius (mm) of the cylinder.
        :param height: Height (mm) of the cylinder wall.
        """
        self._nr_of_angular_points = int(nr_of_angular_points)
        self._nr_of_radial_cap_points = int(nr_of_radial_cap_points)
        self._nr_of_vertical_points = int(nr_of_vertical_points)
        self._cap_spacing = float(cap_spacing)
        self._wall_spacing = float(wall_spacing)
        self._radius = float(radius)
        self._height = float(height)
        self._minimum_radius = 50
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
        """
        Advance to and return the next point on the cylinder surface.

        The generator walks the bottom cap, the wall, and the top cap of the
        current angular sector in turn, then moves to the next sector.

        :return: The next measurement point in cylindrical coordinates.
        :rtype: CylindricalPosition
        :raises Exception: If the internal surface state is inconsistent.
        """
        if self._bottom_cap:
            new_position = self.outwards_cap()

            # if last points of cap, set stuff for wall
            if new_position.r() >= self._radius:
                logger.info('Bottom cap ready; switching to wall')
                self._bottom_cap = False
                self._wall = True
                self._top_cap = False
            return new_position
        elif self._wall:
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
            new_position = self.outwards_cap()

            # if last position of cap, get ready for new angle
            if new_position.r() >= self._radius:
                logger.info('Top cap ready, switching to new angle')
                self._bottom_cap = True
                self._wall = False
                self._top_cap = False
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
        """
        Compute the next zigzag point while sweeping a cap outwards.

        The path alternates between an inner and an outer vertical offset while
        stepping the radius outwards, keeping the move on the cap surface.

        :return: The next cap point in cylindrical coordinates.
        :rtype: CylindricalPosition
        """
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
        """
        Compute the next zigzag point while climbing the cylinder wall.

        The path alternates between stepping radially out and stepping up while
        moving in, keeping the move against the wall surface.

        :return: The next wall point in cylindrical coordinates.
        :rtype: CylindricalPosition
        """
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
        """
        Reset the generator (no-op; state is not rewound for this generator).
        """
        pass

    def ready(self) -> bool:
        """
        Report whether all angular sectors have been generated.

        :return: True once the last sector is complete, False otherwise.
        :rtype: bool
        """
        return self._ready

    def total_points(self) -> int:
        """
        Return the total number of points generated over the whole cylinder.

        :return: The total number of measurement points.
        :rtype: int
        """
        # Total points = nr_of_angular_points * (nr_of_radial_cap_points * 2 + nr_of_vertical_points)
        # However, the code generates TWO points for each radial step in the caps 
        # (one inner, one outer) and TWO points for each vertical step in the wall.
        # So it's actually nr_of_angular_points * 2 * (nr_of_radial_cap_points * 2 + nr_of_vertical_points)
        return self._nr_of_angular_points * 2 * (2 * self._nr_of_radial_cap_points + self._nr_of_vertical_points)


def register(factory) -> None:
    """
    Register :class:`CylindricalMeasurementPoints` with the given factory.

    :param factory: The factory used to register the measurement-points type.
    """
    factory.register("CylindricalMeasurementPoints", CylindricalMeasurementPoints)
