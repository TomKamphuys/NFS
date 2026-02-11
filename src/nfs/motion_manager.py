import configparser
import math
from abc import ABC, abstractmethod
from loguru import logger
from nfs import factory

from .datatypes import CylindricalPosition, cyl_to_cart
from .measurement_points import MeasurementPoints
from .scanner import Scanner


class IMotionManager(ABC):
    """
    Interface for implementing a motion manager.

    The IMotionManager class serves as an abstraction for devices or mechanisms
    that perform rotational movements. It defines the mandatory methods
    that any implementing class should provide to handle rotation, reset
    the rotational position, and shut down the mechanism.
    """

    @abstractmethod
    def move_to_safe_starting_radius(self) -> None:
        pass

    @abstractmethod
    def next(self) -> CylindricalPosition:
        pass

    @abstractmethod
    def ready(self) -> bool:
        pass

    @abstractmethod
    def reset(self):
        pass

    @abstractmethod
    def shutdown(self) -> None:
        pass


class CylindricalMeasurementMotionManager(IMotionManager):
    TOLERANCE = 0.1

    def __init__(self, scanner: Scanner, measurement_points: MeasurementPoints):
        self._scanner = scanner
        self._measurement_points = measurement_points

    def move_to_safe_starting_radius(self) -> None:
        radius = 300  # self._measurement_points.get_radius() TODO MPOT
        logger.info(f'Performing a first move to a safe radius: {radius:.1f}mm')
        self._scanner.planar_move_to(radius, 0.0)

    def next(self) -> CylindricalPosition:
        position = self._measurement_points.next()
        self._move_to_next_measurement_point(position)  # TODO
        return position

    def ready(self) -> bool:
        return self._measurement_points.ready()

    def reset(self):
        self._measurement_points.reset()

    def shutdown(self) -> None:
        self._scanner.shutdown()

    def _move_to_next_measurement_point(self, position: CylindricalPosition) -> None:
        current_position = self._scanner.get_position()
        logger.info(f'Moving: {current_position} --> {position}')
        self._perform_angular_move(position)
        self._perform_planar_move(position)

    def _perform_angular_move(self, position: CylindricalPosition) -> None:
        """
        Performs an angular move of the scanner to the specified position if the difference
        between the current and desired positions exceeds the predefined tolerance.

        :param position: The target position in cylindrical coordinates where the angular move
            is desired.
        :type position: CylindricalPosition
        :return: This function does not return a value.
        :rtype: None
        """
        current_position = self._scanner.get_position()

        if abs(current_position.t() - position.t()) > self.TOLERANCE:
            logger.debug(f'Performing an angular move from {current_position.t():.1f}° to {position.t():.1f}°')
            self._scanner.angular_move_to(position.t())
        else:
            logger.debug('No angular move needed.')

    def _perform_planar_move(self, position: CylindricalPosition) -> None:
        """
        Performs a move in the R-Z plane.
        Ensures the scanner moves along the cylindrical surface (Manhattan-like move)
        rather than cutting corners or moving through the cylinder volume.

        Strategy:
        - Vertical movement (changing Z) is only performed at the safe outer radius (R_safe).
        - Radial movement (changing R) is performed at the current Z (if Z is constant) or
          as part of the sequence to reach R_safe.
        """
        current_position = self._scanner.get_position()
        target_r = position.r()
        target_z = position.z()

        r_diff = abs(current_position.r() - target_r)
        z_diff = abs(current_position.z() - target_z)

        if r_diff <= self.TOLERANCE and z_diff <= self.TOLERANCE:
            logger.debug('No planar move needed.')
            return

        safe_radius = 87.0  # TODO MPOT

        # Strategy:
        # 1. If Z needs to change, we MUST be at safe_radius first.
        # 2. Then change Z.
        # 3. Then move to target R.

        if z_diff > self.TOLERANCE:
            # Step 1: Move to Safe Radius if not already there
            if current_position.r() < safe_radius - self.TOLERANCE:
                logger.debug(f'Moving out to safe radius: R->{safe_radius:.1f}')
                self._scanner.radial_move_to(safe_radius)

            # Step 2: Move Z
            logger.debug(f'Moving Z at safe radius: Z->{target_z:.1f}')
            self._scanner.vertical_move_to(target_z)

            # Step 3: Move to Target Radius if not already there
            current_r = self._scanner.get_position().r()
            if abs(target_r - current_r) > self.TOLERANCE:
                logger.debug(f'Moving to target radius: R->{target_r:.1f}')
                self._scanner.radial_move_to(target_r)

        else:
            # Z is constant, so we are just moving radially on a cap (or wall).
            # Just move R.
            logger.debug(f'Moving R (Z constant): R->{target_r:.1f}')
            self._scanner.radial_move_to(target_r)


class SphericalMeasurementMotionManager(IMotionManager):
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
    :type _scanner: IScanner
    :ivar _measurement_points: The collection of measurement points
        that dictate the scanner's movement.
    :type _measurement_points: MeasurementPoints
    """

    DEGREE_CONVERSION_FACTOR = 180.0 / math.pi
    TOLERANCE = 0.1

    def __init__(self, scanner: Scanner, measurement_points: MeasurementPoints):
        self._scanner = scanner
        self._measurement_points = measurement_points

    def move_to_safe_starting_radius(self) -> None:
        """
        Moves the scanner to a safe starting position at a specified radius.

        This method performs an initial movement of the scanner to a predefined
        safe radius that allows subsequent operations to proceed without
        interference or collision risks. The radius value is retrieved from the
        scanner's measurement points configuration.

        :return: None
        """
        radius = self._measurement_points.get_radius()
        logger.info(f'Performing a first move to a safe radius: {radius:.1f}mm')
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

    def reset(self):
        self._measurement_points.reset()

    def shutdown(self) -> None:
        """
        Shuts down the scanner instance.

        This method invokes the `shutdown` method of the scanner instance, ensuring
        proper cleanup and termination of any processes or resources associated
        with it.

        :raises RuntimeError: If the scanner instance fails to shut down properly.
        :return: None
        """
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
        logger.info(f'Moving: {current_position} --> {position}')
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
        """
        Performs a circular arc move from the scanner's current position to the given
        target position. It determines the direction of the movement (clockwise or
        counterclockwise) based on the angular positions of the current and target
        positions.

        :param position: Target cylindrical position for the arc move.
        :type position: CylindricalPosition
        :return: None
        :rtype: NoneType
        """
        current_position = self._scanner.get_position()
        radius = position.length()
        old_angle_deg = self._calculate_angle_degree(current_position.z(), current_position.r())
        new_angle_deg = self._calculate_angle_degree(position.z(), position.r())

        if new_angle_deg > old_angle_deg:
            logger.debug(f'Move using a CW arc move from {old_angle_deg:.1f}° to {new_angle_deg:.1f}°')
            self._scanner.cw_arc_move_to(position.r(), position.z(), radius)
        elif new_angle_deg < old_angle_deg:
            logger.debug(f'Move using a CCW arc move from {old_angle_deg:.1f}° to {new_angle_deg:.1f}°')
            self._scanner.ccw_arc_move_to(position.r(), position.z(), radius)
        else:
            logger.debug('No arc move needed.')

    def _perform_radial_move(self, position: CylindricalPosition) -> None:
        """
        Performs a radial movement in spherical coordinates. If the radial distance
        between the current position and the target position exceeds a threshold value
        (0.1 mm), the function calculates a new set of Cartesian coordinates based on
        the ratio of the distances. It then initiates a planar move to the new calculated
        coordinates. If the radial distance is within the threshold, no movement is
        performed.

        :param position: The target cylindrical position for the radial movement.
        :type position: CylindricalPosition
        :return: None
        """
        current_position = self._scanner.get_position()
        if math.fabs(current_position.length() - position.length()) > 0.1:
            ratio = position.length() / current_position.length()
            x, y, z = cyl_to_cart(current_position)
            x *= ratio
            y *= ratio
            z *= ratio
            x_plane = math.sqrt(x ** 2 + y ** 2)
            logger.debug(
                f'Performing a (spherical) radius move {current_position.length():.1f}mm to {position.length():.1f}mm')
            self._scanner.planar_move_to(x_plane, z)
        else:
            logger.debug('No (spherical) radial move needed.')

    def _perform_angular_move(self, position: CylindricalPosition) -> None:
        """
        Performs an angular move of the scanner to the specified position if the difference
        between the current and desired positions exceeds the predefined tolerance.

        :param position: The target position in cylindrical coordinates where the angular move
            is desired.
        :type position: CylindricalPosition
        :return: This function does not return a value.
        :rtype: None
        """
        current_position = self._scanner.get_position()

        if abs(current_position.t() - position.t()) > self.TOLERANCE:
            logger.debug(f'Performing an angular move from {current_position.t():.1f}° to {position.t():.1f}°')
            self._scanner.angular_move_to(position.t())
        else:
            logger.debug('No angular move needed.')


class MotionManagerFactory:
    @staticmethod
    def create(config_file: str, section: str, scanner: Scanner) -> IMotionManager:

        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        measurement_points_section = config_parser.get(section, 'measurement_points')
        item = dict(config_parser.items(measurement_points_section))
        measurement_points = factory.create(item)

        motion_manager_type = config_parser.get(section, 'type')
        if motion_manager_type == 'CylindricalMeasurementMotionManager':
            return CylindricalMeasurementMotionManager(scanner, measurement_points)
        elif motion_manager_type == 'SphericalMeasurementMotionManager':
            return SphericalMeasurementMotionManager(scanner, measurement_points)
        else:
            raise ValueError(f'Unknown motion manager type: {motion_manager_type}')
