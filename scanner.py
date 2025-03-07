import configparser
import math
from loguru import logger
from ticlib import TicUSB  # type: ignore
import numpy as np
from datatypes import CylindricalPosition, cyl_to_cart
from grbl_controller import GrblControllerFactory, IGrblController
from measurement_points import MeasurementPoints
from rotator import RotatorControllerFactory, IRotator


def has_intersect(plane_normal, ray_direction, epsilon=1e-6) -> bool:
    """
    Determines whether a ray intersects with a plane. The calculation is based on the dot
    product of the plane's normal vector and the ray's direction vector. If the absolute
    value of this dot product is less than a small threshold (epsilon), the ray is
    considered parallel to the plane, and thus does not intersect.

    :param plane_normal: A vector representing the normal of the plane.
    :type plane_normal: numpy.ndarray
    :param ray_direction: A vector representing the direction of the ray.
    :type ray_direction: numpy.ndarray
    :param epsilon: A very small value used as a threshold to determine if the dot
        product is sufficiently close to zero to consider the ray parallel.
    :type epsilon: float
    :return: ``True`` if an intersection occurs; otherwise, ``False``.
    :rtype: bool
    """
    n_dot_u = plane_normal.dot(ray_direction)
    if abs(n_dot_u) < epsilon:
        return False

    return True


def line_plane_intersection(plane_normal, plane_point, ray_direction, ray_point) -> np.array:
    """
    Calculates the intersection point of a ray and a plane defined in 3D space. The function
    performs the computation based on vector mathematics and returns the intersection
    coordinate as a NumPy array. If the ray is parallel to the plane and does not intersect,
    the results may be undefined.

    :param plane_normal: The normal vector of the plane, defining its orientation.
    :param plane_point: A point on the plane used to define its position in 3D space.
    :param ray_direction: The direction vector of the ray, indicating its trajectory.
    :param ray_point: A point on the ray representing its position.
    :return: A NumPy array representing the coordinate of the intersection point.
    """
    n_dot_u = plane_normal.dot(ray_direction)

    w = ray_point - plane_point
    si = -plane_normal.dot(w) / n_dot_u
    psi = w + si * ray_direction + plane_point
    return psi

def is_between(a, b, c):
    """
    Determines whether a given value `b` lies between two other values, `a` and
    `c`. The function checks the order of `a` and `c`, allowing for cases where
    `a` might be greater than `c` or where `a` is less than or equal to `c`.

    :param a: The first boundary value in the comparison.
    :type a: int or float
    :param b: The value to check if it lies between `a` and `c`.
    :type b: int or float
    :param c: The second boundary value in the comparison.
    :type c: int or float
    :return: A boolean indicating if `b` lies between `a` and `c` inclusively.
    :rtype: bool
    """
    if a >= c:
        return a >= b >= c

    return a <= b <= c

def is_vertical_move_safe(current_position, next_position, plane_point_z):
    """
    Determines if a vertical movement between two positions is safe given constraints related to
    a predefined plane. The function checks whether the path between the two positions intersects
    the plane, and if the potential intersection point lies within specific boundaries.

    :param current_position: The starting position of the movement.
    :param next_position: The intended end position of the movement.
    :param plane_point_z: The z-coordinate of a fixed plane against which the movement's safety
        is evaluated.
    :return: A boolean value indicating whether the vertical move is safe (True) or unsafe (False).
    """
    plane_normal = np.array([0, 0, 1])
    move_direction = np.array([0, 0, 1])
    plane_point = np.array([0, 0, plane_point_z])

    x, y, z = cyl_to_cart(current_position)
    move_point = np.array([x, y, z])

    intersection_point = line_plane_intersection(plane_normal, plane_point, move_direction, move_point)

    intersect_is_during_move = is_between(current_position.z(), intersection_point[2], next_position)
    if abs(intersection_point[0]) < 270 / 2 and abs(
            intersection_point[1]) < 195 / 2 and intersect_is_during_move:  # TODO from config
        logger.info(
            f'Unsafe move requested from {x, y, z} [mm] to {x, y, next_position} [mm] (xyz). Reverting to evasive move')
        return False

    return True


def is_radial_move_safe(current_position, next_position):
    """
    Check if a radial move is safe based on the current and next positions.

    The function evaluates whether the move from the given current position
    to the specified next position is within the acceptable radial and positional
    boundaries. If the move is deemed unsafe, it logs the occurrence and triggers
    a revert to an evasive maneuver.

    :param current_position: The current position of the object with coordinates
                             including a z-component.
    :type current_position: Any
    :param next_position: The next calculated radial position to evaluate.
    :type next_position: float
    :return: A boolean value indicating if the radial move is safe
             (True if safe, False otherwise).
    :rtype: bool
    """
    radius = np.sqrt((195 / 2) ** 2 + (270 / 2) ** 2)
    if np.abs(current_position.z()) <= 375 / 2 and next_position < radius:
        logger.info('Unsafe radial move requested. Reverting to evasive move')
        return False

    return True


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
        self._grbl_controller.send_and_wait(f'G02 X{z:.4f} Y{r:.4f} R{radius:.4f} F{self._feed_rate}')

    def ccw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        self._grbl_controller.send_and_wait(f'G03 X{z:.4f} Y{r:.4f} R{radius:.4f} F{self._feed_rate}')

    def move_to_rz(self, r: float, z: float) -> None:
        self._grbl_controller.send_and_wait(f'G0 X{z}Y{r}')

    def move_to_vertical(self, z: float) -> None:
        self._grbl_controller.send_and_wait(f'G0 X{z}')

    def move_to_radial(self, r: float) -> None:
        self._grbl_controller.send_and_wait(f'G0 Y{r}')

    def set_as_zero(self) -> None:
        self._grbl_controller.send('G92 X0 Y0')
        self._grbl_controller.send('$10=0')

    def shutdown(self):
        logger.info('Disconnecting from GRBL')
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

    def radial_move_to(self, r: float) -> None:
        logger.trace(f'Radial move to {r}')
        if self.get_position().r() != r:
            self._planar_mover.move_to_radial(r)
        self._cylindrical_position.set_r(r)

    def planar_move_to(self, r: float, z: float):
        self._planar_mover.move_to_rz(r, z)

    def cw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        self._planar_mover.cw_arc_move_to(r, z, radius)

    def ccw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        self._planar_mover.ccw_arc_move_to(r, z, radius)

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
        self.angular_move_to(self._cylindrical_position.t() + amount)

    def rotate_clockwise(self, amount: float) -> None:
        self.rotate_counterclockwise(-amount)

    def move_out(self, amount: float) -> None:
        self.radial_move_to(self._cylindrical_position.r() + amount)

    def move_in(self, amount: float) -> None:
        self.move_out(-amount)

    def move_up(self, amount: float) -> None:
        self.vertical_move_to(self._cylindrical_position.z() + amount)

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
    :ivar _current_position: Tracks the current position of the device in cylindrical
        coordinates, updated during movement.
    :type _current_position: CylindricalPosition
    """
    def __init__(self, scanner: Scanner, measurement_points: MeasurementPoints):
        self._scanner = scanner
        self._measurement_points = measurement_points
        self._current_position = CylindricalPosition(320, 0.0, 0.0)

    def move_to_safe_starting_position(self) -> None:
        radius = self._measurement_points.get_radius()
        logger.info(f'Performing a first move to a safe radius: {radius} mm')
        self._scanner.planar_move_to(radius, 0.0)
        self._current_position = CylindricalPosition(radius, 0.0, 0.0)

    def next(self) -> CylindricalPosition:
        position = self._measurement_points.next()
        self._move_to_next_measurement_point(position)
        return position

    def ready(self) -> bool:
        return self._measurement_points.ready()

    def shutdown(self):
        self._scanner.shutdown()

    def _move_to_next_measurement_point(self, position: CylindricalPosition) -> None:

        logger.info(f'Moving to {position} from {self._current_position}')

        if self._current_position.t() != position.t():
            logger.debug(f'Performing an angular move from {self._current_position.t()} degrees to {position.t()} degrees')
            self._scanner.angular_move_to(position.t())
            self._current_position.set_t(position.t())
        else:
            logger.debug('No Angular move needed.')

        if math.fabs(self._current_position.length() - position.length()) > 0.1:
            ratio = position.length() / self._current_position.length()
            x, y, z = cyl_to_cart(self._current_position)
            x *= ratio
            y *= ratio
            z *= ratio
            x_plane = math.sqrt(x ** 2 + y ** 2)
            logger.debug(f'Performing a (spherical) radius move {self._current_position.length()} mm to {position.length()} mm')
            self._scanner.planar_move_to(x_plane, z)
            self._current_position.set_r(x_plane)
            self._current_position.set_z(z)
        else:
            logger.debug('No (spherical) radial move needed.')

        radius = position.length()
        old_angle = np.around(math.atan2(self._current_position.z(), self._current_position.r()) / math.pi * 180.0, 2)
        new_angle = np.around(math.atan2(position.z(), position.r()) / math.pi * 180.0, 2)
        if new_angle > old_angle:
            logger.debug(f'Move using an CW arc move from {old_angle:.2f} to {new_angle:.2f} degrees')
            self._scanner.cw_arc_move_to(position.r(), position.z(), radius)
        elif new_angle < old_angle:
            logger.debug(f'Move using an CCW arc move from {old_angle:.2f} to {new_angle:.2f} degrees')
            self._scanner.ccw_arc_move_to(position.r(), position.z(), radius)
        else:
            logger.debug('No arc move needed')

        self._current_position = position


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
        section = 'grbl'
        feed_rate = config_parser.getfloat(section, 'feed_rate')
        controller_section = config_parser.get(section, 'controller_section')

        grbl_controller = GrblControllerFactory.create(controller_section, config_file)

        rotation_controller = config_parser.get('scanner', 'rotation_controller')
        angular_mover = RotatorControllerFactory.create(rotation_controller, config_file)

        planar_mover = PlanarMover(grbl_controller, feed_rate)

        scanner = Scanner(planar_mover, angular_mover)

        return scanner
