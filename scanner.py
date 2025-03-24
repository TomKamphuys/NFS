import configparser
import math

from loguru import logger

from datatypes import CylindricalPosition, cyl_to_cart
from grbl_controller import GrblControllerFactory, IGrblController
from measurement_points import MeasurementPoints
from rotator import RotatorFactory, IRotator


class PlanarMover:
    """
    Provides functionality to control movement in a planar system using a GRBL controller.

    This class interacts with a GRBL controller to execute movement commands in a planar
    coordinate system. It supports various movement types such as circular clockwise arcs,
    counterclockwise arcs, and linear movements in radial and vertical directions.
    It is designed to be used in scenarios where precise control over movement is required.

    :ivar _grbl_controller: Instance of the GRBL controller used to send movement commands.
    :type _grbl_controller: IGrblController
    :ivar _feed_rate: The feed rate for motions in the system.
    :type _feed_rate: float
    """
    def __init__(self, grbl_controller: IGrblController, feed_rate: float):
        self._feed_rate = feed_rate
        self._grbl_controller = grbl_controller

    def get_position(self) -> CylindricalPosition:
        # bit weird, but mapping from x,y to r, z
        return CylindricalPosition(self._grbl_controller.get_position().t(), 0.0, self._grbl_controller.get_position().r())

    def cw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        self._grbl_controller.send_and_wait_for_move_ready(f'G02 X{z:.4f} Y{r:.4f} R{radius:.4f} F{self._feed_rate}')

    def ccw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        self._grbl_controller.send_and_wait_for_move_ready(f'G03 X{z:.4f} Y{r:.4f} R{radius:.4f} F{self._feed_rate}')

    def move_to_rz(self, r: float, z: float) -> None:
        self._grbl_controller.send_and_wait_for_move_ready(f'G0 X{z}Y{r}')

    def move_to_vertical(self, z: float) -> None:
        self._grbl_controller.send_and_wait_for_move_ready(f'G0 X{z}')

    def move_to_radial(self, r: float) -> None:
        self._grbl_controller.send_and_wait_for_move_ready(f'G0 Y{r}')

    def set_as_zero(self) -> None:
        self._grbl_controller.send('G92 X0 Y0')
        self._grbl_controller.send('$10=0')

    def shutdown(self):
        self._grbl_controller.shutdown()


class Scanner:
    """
    Controls the movement of a scanner in cylindrical coordinates (radial, angular, vertical).
    Combines planar and angular movement capabilities and manages scanner's position.
    """
    ZERO_POSITION = (0, 0, 0)  # Constant for zeroed coordinates (r, theta, z)

    def __init__(self, planar_mover: PlanarMover, angular_mover: IRotator):
        self._planar_mover = planar_mover
        self._angular_mover = angular_mover
        self._cylindrical_position = CylindricalPosition(*self.ZERO_POSITION)
        self.set_as_zero()

    def radial_move_to(self, r: float) -> None:
        """Move to specified radial position."""
        if self.get_position().r() != r:
            self._planar_mover.move_to_radial(r)
        self._update_position(r=r)

    def planar_move_to(self, r: float, z: float):
        """Move to a specified planar position (radial + vertical)."""
        self._planar_mover.move_to_rz(r, z)
        self._update_position(r=r, z=z)

    def cw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        """Move in a clockwise arc to the specified radial and vertical position."""
        self._planar_mover.cw_arc_move_to(r, z, radius)
        self._update_position(r=r, z=z)

    def ccw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        """Move in a counter-clockwise arc to the specified radial and vertical position."""
        self._planar_mover.ccw_arc_move_to(r, z, radius)
        self._update_position(r=r, z=z)

    def angular_move_to(self, angle: float) -> None:
        """Rotate to a specified angular position."""
        if self.get_position().t() != angle:
            self._angular_mover.move_to(angle)
        self._update_position(t=angle)

    def vertical_move_to(self, z: float) -> None:
        """Move to a specified vertical position."""
        if self.get_position().z() != z:
            self._planar_mover.move_to_vertical(z)
        self._update_position(z=z)

    def rotate_ccw(self, amount: float) -> None:
        """Rotate counterclockwise by a specified amount."""
        self.angular_move_to(self._cylindrical_position.t() + amount)

    def rotate_cw(self, amount: float) -> None:
        """Rotate clockwise by a specified amount."""
        self.rotate_ccw(-amount)

    def move_out(self, amount: float) -> None:
        """Increase radial distance by a specified amount."""
        self.radial_move_to(self._cylindrical_position.r() + amount)

    def move_in(self, amount: float) -> None:
        """Decrease radial distance by a specified amount."""
        self.move_out(-amount)

    def move_up(self, amount: float) -> None:
        """Increase vertical position by a specified amount."""
        self.vertical_move_to(self._cylindrical_position.z() + amount)

    def move_down(self, amount: float) -> None:
        """Decrease vertical position by a specified amount."""
        self.move_up(-amount)

    def get_position(self) -> CylindricalPosition:
        """Return the current cylindrical position."""
        t = self._angular_mover.get_position()
        rz = self._planar_mover.get_position()
        return CylindricalPosition(rz.r(), t.t(), rz.z())

    def set_as_zero(self) -> None:
        """Reset scanner to the zero position."""
        self._planar_mover.set_as_zero()
        self._angular_mover.set_as_zero()
        self._update_position(*self.ZERO_POSITION)

    def shutdown(self) -> None:
        """Shutdown the scanner's movers."""
        self._planar_mover.shutdown()
        self._angular_mover.shutdown()

    def _update_position(self, r: float = None, t: float = None, z: float = None) -> None:
        """Update the specified cylindrical position components."""
        if r is not None:
            self._cylindrical_position.set_r(r)
        if t is not None:
            self._cylindrical_position.set_t(t)
        if z is not None:
            self._cylindrical_position.set_z(z)


class SphericalMeasurementMotionManager:
    """
    Manages the motion of a scanner for spherical measurements.

    This class oversees the movement of a scanner to specified
    spherical measurement points using cylindrical coordinates.
    It ensures that the scanner moves safely to predetermined
    positions and transitions between measurement points
    efficiently while maintaining accuracy. The class also
    provides operations for shutting down the scanner gracefully.

    :ivar _scanner: The scanner instance responsible for performing
        the physical movements.
    :type _scanner: Scanner
    :ivar _measurement_points: The collection of measurement points
        that dictate the scanner's movement.
    :type _measurement_points: MeasurementPoints
    """

    DEGREE_CONVERSION_FACTOR = 180.0 / math.pi

    def __init__(self, scanner: Scanner, measurement_points: MeasurementPoints):
        self._scanner = scanner
        self._measurement_points = measurement_points

    def move_to_safe_starting_position(self) -> None:
        """
        Moves the scanner to a safe starting position at a specified radius.

        This method performs an initial movement of the scanner to a predefined
        safe radius that allows subsequent operations to proceed without
        interference or collision risks. The radius value is retrieved from the
        scanner's measurement points configuration.

        :return: None
        """
        radius = self._measurement_points.get_radius()
        logger.info(f'Performing a first move to a safe radius: {radius} mm')
        self._scanner.planar_move_to(radius, 0.0)

    def next(self) -> CylindricalPosition:
        """
        Advances to the next measurement point and moves to its position.

        This method retrieves the next position from the sequence of measurement points,
        moves to the specified position using an internal process, and then
        returns the position object.

        :return: The next cylindrical position object from the measurement points
            sequence.
        :rtype: CylindricalPosition
        """
        position = self._measurement_points.next()
        self._move_to_next_measurement_point(position)
        return position

    def ready(self) -> bool:
        return self._measurement_points.ready()

    def shutdown(self) -> None:
        self._scanner.shutdown()

    def _move_to_next_measurement_point(self, position: CylindricalPosition) -> None:
        """
        Move the scanner to the specified next measurement point in the cylindrical
        coordinate system. This method performs a sequence of angular, radial, and
        circular arc movements to reach the target position. The movement ensures
        precision in transitioning to the target point while maintaining the integrity
        of the scanning process.

        :param position: The target position to which the scanner should move in
            cylindrical coordinates.
        :type position: CylindricalPosition
        :return: This method does not return a value.
        :rtype: None
        """
        current_position = self._scanner.get_position()
        logger.info(f'Moving to {position} from {current_position}')
        self._perform_angular_move(position)
        self._perform_radial_move(position)
        self._perform_circular_arc_move(position)

    def _calculate_angle_degree(self, z: float, r: float) -> float:
        """
        Calculates the angle in degrees from the given z and r coordinates.
        :param z: The z-coordinate.
        :param r: The radial distance.
        :return: The angle in degrees.
        """
        return round(math.atan2(z, r) * self.DEGREE_CONVERSION_FACTOR, 2)

    def _perform_circular_arc_move(self, position: CylindricalPosition) -> None:
        current_position = self._scanner.get_position()
        radius = position.length()
        old_angle_deg = self._calculate_angle_degree(current_position.z(), current_position.r())
        new_angle_deg = self._calculate_angle_degree(position.z(), position.r())

        if new_angle_deg > old_angle_deg:
            logger.debug(f'Move using a CW arc move from {old_angle_deg:.2f}° to {new_angle_deg:.2f}°')
            self._scanner.cw_arc_move_to(position.r(), position.z(), radius)
        elif new_angle_deg < old_angle_deg:
            logger.debug(f'Move using a CCW arc move from {old_angle_deg:.2f}° to {new_angle_deg:.2f}°')
            self._scanner.ccw_arc_move_to(position.r(), position.z(), radius)
        else:
            logger.debug('No arc move needed.')


    def _perform_radial_move(self, position: CylindricalPosition) -> None:
        current_position = self._scanner.get_position()
        if math.fabs(current_position.length() - position.length()) > 0.1:
            ratio = position.length() / current_position.length()
            x, y, z = cyl_to_cart(current_position)
            x *= ratio
            y *= ratio
            z *= ratio
            x_plane = math.sqrt(x ** 2 + y ** 2)
            logger.debug(
                f'Performing a (spherical) radius move {current_position.length()} mm to {position.length()} mm')
            self._scanner.planar_move_to(x_plane, z)
        else:
            logger.debug('No (spherical) radial move needed.')

    def _perform_angular_move(self, position: CylindricalPosition) -> None:
        current_position = self._scanner.get_position()

        if current_position.t() != position.t():
            logger.debug(f'Performing an angular move from {current_position.t()} degrees to {position.t()} degrees')
            self._scanner.angular_move_to(position.t())
        else:
            logger.debug('No Angular move needed.')


class ScannerFactory:
    """
    Factory for creating Scanner objects.

    This class provides a mechanism to create and configure a Scanner object
    using a configuration file. It reads the configuration file, initializes
    necessary components such as angular and planar movers, and builds the
    Scanner object.

    :ivar config_file: The path to the configuration file used to initialize
        the Scanner.
    :type config_file: str
    """
    @staticmethod
    def create(config_file: str) -> Scanner:

        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)
        section = 'scanner'

        rotation_controller = config_parser.get(section, 'rotator')
        angular_mover = RotatorFactory.create(rotation_controller, config_file)

        controller_section = config_parser.get(section, 'planar_mover_controller')
        grbl_controller = GrblControllerFactory.create(controller_section, config_file)
        feed_rate = config_parser.getfloat(section, 'feed_rate')
        planar_mover = PlanarMover(grbl_controller, feed_rate)

        scanner = Scanner(planar_mover, angular_mover)

        return scanner
