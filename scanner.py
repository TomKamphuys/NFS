import configparser
import math
from loguru import logger
import numpy as np
from datatypes import CylindricalPosition, cyl_to_cart
from grbl_controller import GrblControllerFactory, IGrblController
from measurement_points import MeasurementPoints
from rotator import RotatorFactory, IRotator


class PlanarMover:
    """
    Handles GRBL axis-related operations and commands for CNC machines.

    This class provides an interface to send commands specific to axis control,
    manage zero settings, perform arc movements (clockwise and counterclockwise),
    and move to specified positions based on R and Z coordinates. It interacts
    with a GRBL controller object for executing these commands effectively.

    """
    def __init__(self, grbl_controller: IGrblController, feed_rate: float):
        self._feed_rate = feed_rate
        self._grbl_controller = grbl_controller

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
    Provides functionality for controlling a scanner with radial, angular, and vertical
    movement capabilities. Encapsulates motion control for cylindrical coordinate systems.

    The Scanner class manages movement along radial, angular, and vertical axes while
    maintaining the current position in cylindrical coordinates. It also provides utility
    methods for adjustments, setting the position to zero, and shutting down the system
    safely. This class ensures smooth control through abstraction over lower-level motion
    controllers provided externally.

    :ivar _planar_mover: Low-level controller for planar movement.
    :type _planar_mover: Any
    :ivar _angular_mover: Low-level controller for angular movement.
    :type _angular_mover: Any
    :ivar _cylindrical_position: The current position of the scanner in cylindrical
        coordinates, represented internally.
    :type _cylindrical_position: CylindricalPosition
    """
    def __init__(self, planar_mover: PlanarMover, angular_mover: IRotator):
        self._planar_mover = planar_mover
        self._angular_mover = angular_mover
        self._cylindrical_position = CylindricalPosition(0, 0, 0)
        self.set_as_zero()

    def radial_move_to(self, r: float) -> None:
        logger.trace(f'Radial move to {r}')
        if self.get_position().r() != r:
            self._planar_mover.move_to_radial(r)
        self._cylindrical_position.set_r(r)

    def planar_move_to(self, r: float, z: float):
        self._planar_mover.move_to_rz(r, z)
        self._cylindrical_position.set_r(r)
        self._cylindrical_position.set_z(z)

    def cw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        self._planar_mover.cw_arc_move_to(r, z, radius)
        self._cylindrical_position.set_r(r)
        self._cylindrical_position.set_z(z)

    def ccw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        self._planar_mover.ccw_arc_move_to(r, z, radius)
        self._cylindrical_position.set_r(r)
        self._cylindrical_position.set_z(z)

    def angular_move_to(self, angle: float) -> None:
        logger.trace(f'Angular move to {angle}')
        if self.get_position().t() != angle:
            self._angular_mover.move_to(angle)
        self._cylindrical_position.set_t(angle)

    def vertical_move_to(self, z: float) -> None:
        logger.trace(f'Vertical move to {z}')
        if self.get_position().z() != z:
            self._planar_mover.move_to_vertical(z)
        self._cylindrical_position.set_z(z)

    def rotate_counterclockwise(self, amount: float) -> None:
        new_value = self._cylindrical_position.t() + amount
        self._cylindrical_position.set_t(new_value)
        self.angular_move_to(new_value)

    def rotate_clockwise(self, amount: float) -> None:
        self.rotate_counterclockwise(-amount)

    def move_out(self, amount: float) -> None:
        new_value = self._cylindrical_position.r() + amount
        self._cylindrical_position.set_r(new_value)
        self.radial_move_to(new_value)

    def move_in(self, amount: float) -> None:
        self.move_out(-amount)

    def move_up(self, amount: float) -> None:
        new_value = self._cylindrical_position.z() + amount
        self._cylindrical_position.set_z(new_value)
        self.vertical_move_to(new_value)

    def move_down(self, amount: float) -> None:
        self.move_up(-amount)

    def get_position(self) -> CylindricalPosition:
        return self._cylindrical_position

    def set_as_zero(self) -> None:
        self._planar_mover.set_as_zero()
        self._angular_mover.set_as_zero()
        self._cylindrical_position.set_r(0)
        self._cylindrical_position.set_t(0)
        self._cylindrical_position.set_z(0)

    def shutdown(self) -> None:
        self._planar_mover.shutdown()
        self._angular_mover.shutdown()


class SphericalMeasurementMotionManager:
    """
    Manages motion for taking spherical measurements.

    This class provides functionality to move a device to specified measurement
    points in a spherical coordinate system. It ensures safe starting positions and
    handles angular, radial, and arc movements as necessary.

    :ivar _measurement_points: Object holding details about the sequence of
        measurement points. Provides information for current and next points.
    :type _measurement_points: MeasurementPoints
    """
    def __init__(self, scanner: Scanner, measurement_points: MeasurementPoints):
        self._scanner = scanner
        self._measurement_points = measurement_points

    def move_to_safe_starting_position(self) -> None:
        radius = self._measurement_points.get_radius()
        logger.info(f'Performing a first move to a safe radius: {radius} mm')
        self._scanner.planar_move_to(radius, 0.0)

    def next(self) -> CylindricalPosition:
        position = self._measurement_points.next()
        self._move_to_next_measurement_point(position)
        return position

    def ready(self) -> bool:
        return self._measurement_points.ready()

    def shutdown(self) -> None:
        self._scanner.shutdown()

    def _move_to_next_measurement_point(self, position: CylindricalPosition) -> None:
        current_position = self._scanner.get_position()
        logger.info(f'Moving to {position} from {current_position}')

        self._perform_angular_move(position)
        self._perform_radial_move(position)
        self._perform_arc_move(position)

    def _perform_arc_move(self, position: CylindricalPosition) -> None:
        current_position = self._scanner.get_position()
        radius = position.length()
        old_angle = np.around(math.atan2(current_position.z(), current_position.r()) / math.pi * 180.0, 2)
        new_angle = np.around(math.atan2(position.z(), position.r()) / math.pi * 180.0, 2)
        if new_angle > old_angle:
            logger.debug(f'Move using an CW arc move from {old_angle:.2f} to {new_angle:.2f} degrees')
            self._scanner.cw_arc_move_to(position.r(), position.z(), radius)
        elif new_angle < old_angle:
            logger.debug(f'Move using an CCW arc move from {old_angle:.2f} to {new_angle:.2f} degrees')
            self._scanner.ccw_arc_move_to(position.r(), position.z(), radius)
        else:
            logger.debug('No arc move needed')

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
    Factory class for creating Scanner objects.

    This class provides a static method to create and configure a Scanner instance
    using a configuration file. The method initializes all necessary components
    required to construct the Scanner, such as motion controllers, measurement
    points, and a measurement motion manager.

    Static Methods:
        create: Creates a Scanner instance with the specified configuration.

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
