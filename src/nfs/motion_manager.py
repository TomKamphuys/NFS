import configparser
import math
from abc import ABC, abstractmethod
from loguru import logger
from nfs import factory

from .datatypes import CylindricalPosition
from .utils.geometry import cyl_to_cart
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
        """
        Move the scanner to a safe starting radial position.
        """
        pass

    @abstractmethod
    def next(self) -> CylindricalPosition:
        """
        Move to the next measurement position and return it.

        :return: The next CylindricalPosition.
        """
        pass

    @abstractmethod
    def ready(self) -> bool:
        """
        Check if the motion manager is ready or has finished all points.

        :return: True if finished, False otherwise.
        """
        pass

    @abstractmethod
    def reset(self):
        """
        Reset the motion manager to the beginning of the measurement set.
        """
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """
        Shut down the motion manager and its associated scanner.
        """
        pass

    @abstractmethod
    def total_points(self) -> int:
        """
        Return the total number of measurement points.

        :return: Total points as an integer.
        """
        pass


class CylindricalMeasurementMotionManager(IMotionManager):
    """
    Manages motion for cylindrical measurement sets.
    """
    TOLERANCE = 0.1

    def __init__(self, scanner: Scanner, measurement_points: MeasurementPoints, safe_radius: float):
        """
        Initialize the cylindrical motion manager.

        :param scanner: The scanner instance to control.
        :param measurement_points: The set of points to measure.
        :param safe_radius: The radial distance considered safe for vertical moves.
        """
        self._scanner = scanner
        self._measurement_points = measurement_points
        self._safe_radius = safe_radius

    def move_to_safe_starting_radius(self) -> None:
        """
        Move to the safe starting radius and zero height.
        """
        logger.info(f'Performing a first move to a safe radius: {self._safe_radius:.1f}mm')
        self._scanner.planar_move_to(self._safe_radius, 0.0)

    def next(self) -> CylindricalPosition:
        """
        Move to the next cylindrical measurement point.

        :return: The target CylindricalPosition.
        """
        position = self._measurement_points.next()
        self._move_to_next_measurement_point(position)  # TODO
        return position

    def ready(self) -> bool:
        """
        Check if all points have been measured.

        :return: True if ready (all points done), False otherwise.
        """
        return self._measurement_points.ready()

    def reset(self):
        """
        Reset the point sequence.
        """
        self._measurement_points.reset()

    def shutdown(self) -> None:
        """
        Shut down the scanner.
        """
        self._scanner.shutdown()

    def total_points(self) -> int:
        """
        Get the total number of points in the set.

        :return: Total points.
        """
        return self._measurement_points.total_points()

    def _move_to_next_measurement_point(self, position: CylindricalPosition) -> None:
        """
        Internal method to execute moves to the next point.

        :param position: The target CylindricalPosition.
        """
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

        # Strategy:
        # 1. If Z needs to change, we MUST be at safe_radius first.
        # 2. Then change Z.
        # 3. Then move to target R.

        if z_diff > self.TOLERANCE:
            # Step 1: Move to Safe Radius if not already there
            if current_position.r() < self._safe_radius - self.TOLERANCE:
                logger.debug(f'Moving out to safe radius: R->{self._safe_radius:.1f}')
                self._scanner.radial_move_to(self._safe_radius)

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


class FastCylindricalMeasurementMotionManager(IMotionManager):
    """
    Fast, concurrent-yet-safe motion manager for cylindrical measurement sets.

    Rationale
    ---------
    The original :class:`CylindricalMeasurementMotionManager` is provably safe
    but slow: it retracts the arm all the way to ``safe_radius`` and then runs
    three *sequential* moves (retract R -> move Z -> move R) for **every** point
    whose Z coordinate changes. Because the cap and wall scans are zig-zags in
    the R/Z plane, virtually every measurement point changes Z, so almost every
    step pays for a full out-and-back detour.

    This manager keeps the exact same safety guarantee but removes the
    unnecessary detours by exploiting two geometric facts of the cylindrical
    point generator (:class:`CylindricalMeasurementPoints`) and the grid generator
    used in the GUI that injects points via the FileMeasurementPoints class:

    1. **Consecutive points on the same measurement surface** (the bottom cap,
       the wall, or the top cap) are adjacent zig-zag steps. Machine axes move
       by *linear interpolation*, so along a single ``G0`` move the radius
       ``r(s) = r_start + s * (r_end - r_start)`` is monotonic in the path
       parameter ``s in [0, 1]``. Hence the radius never dips below
       ``min(r_start, r_end)``. Since both endpoints lie on the measurement
       surface (``r >= minimum measurement radius`` and Z inside the local cap
       band or on the wall), the straight-line move stays on/against that
       surface and never crosses the protected interior of the grid. Such moves
       are therefore executed as a **single simultaneous 3-axis move**.

    2. **The only transition that would cut through the interior volume** is the
       jump from the end of the top cap ``(radius, theta_old, height)`` to the
       start of the next angular sector's bottom cap
       ``(minimum_radius, theta_new, 0)``. The point generator flags exactly
       this transition via :meth:`need_to_do_evasive_move`. For it we perform
       the classic safe maneuver, entirely **outside** the measurement cylinder:

           a. retract radially to ``safe_radius`` (>= grid radius) at the
              current Z/theta;
           b. simultaneously rotate to ``theta_new`` and travel to the target Z
              while staying at ``safe_radius`` (the whole vertical sweep and the
              rotation happen outside the grid);
           c. move radially inward to the target radius at the target Z.

    Safety invariant
    ----------------
    The microphone arm never enters the volume enclosed by the measurement
    cylinder. Every interior-crossing transition is routed around the outside at
    ``safe_radius``; every other move stays on a measurement surface where the
    linearly-interpolated radius is bounded below by its endpoints.

    :ivar _scanner: The scanner instance that performs the physical movements.
    :ivar _measurement_points: The collection of measurement points.
    :ivar _safe_radius: Radius (mm), >= grid radius, used for interior-crossing
        transitions.
    """
    TOLERANCE = 0.1

    def __init__(self, scanner: Scanner, measurement_points: MeasurementPoints, safe_radius: float):
        """
        Initialize the fast cylindrical motion manager.

        :param scanner: The scanner instance to control.
        :param measurement_points: The set of points to measure.
        :param safe_radius: The radial distance (>= grid radius) used for
            interior-crossing transitions.
        """
        self._scanner = scanner
        self._measurement_points = measurement_points
        self._safe_radius = safe_radius

    def move_to_safe_starting_radius(self) -> None:
        """
        Move to the safe starting radius and zero height.
        """
        logger.info(f'Performing a first move to a safe radius: {self._safe_radius:.1f}mm')
        self._scanner.planar_move_to(self._safe_radius, 0.0)

    def next(self) -> CylindricalPosition:
        """
        Move to the next cylindrical measurement point.

        :return: The target CylindricalPosition.
        """
        position = self._measurement_points.next()
        evasive = self._measurement_points.need_to_do_evasive_move()
        self._move_to_next_measurement_point(position, evasive)
        return position

    def ready(self) -> bool:
        """
        Check if all points have been measured.

        :return: True if ready (all points done), False otherwise.
        """
        return self._measurement_points.ready()

    def reset(self):
        """
        Reset the point sequence.
        """
        self._measurement_points.reset()

    def shutdown(self) -> None:
        """
        Shut down the scanner.
        """
        self._scanner.shutdown()

    def total_points(self) -> int:
        """
        Get the total number of points in the set.

        :return: Total points.
        """
        return self._measurement_points.total_points()

    def _move_to_next_measurement_point(self, position: CylindricalPosition, evasive: bool) -> None:
        """
        Execute the move to the next point, picking the safe strategy.

        :param position: The target CylindricalPosition.
        :param evasive: True when the point generator signals an
            interior-crossing transition that must be routed around the outside.
        """
        current_position = self._scanner.get_position()
        logger.info(f'Moving: {current_position} --> {position} (evasive={evasive})')

        if evasive:
            logger.info(f'Evasive move')
            self._perform_evasive_move(position)
        else:
            self._perform_direct_move(current_position, position)

    def _perform_direct_move(self, current_position: CylindricalPosition,
                             position: CylindricalPosition) -> None:
        """
        Move all changed axes simultaneously in a single rapid command.

        This is only used between consecutive points that lie on the same
        measurement surface (cap or wall). As argued in the class docstring, the
        linearly-interpolated radius stays >= min(start_r, end_r), so the arm
        never crosses the protected interior.

        :param current_position: The scanner's current position.
        :param position: The target CylindricalPosition.
        """
        r_diff = abs(current_position.r() - position.r())
        t_diff = abs(current_position.t() - position.t())
        z_diff = abs(current_position.z() - position.z())

        if r_diff <= self.TOLERANCE and t_diff <= self.TOLERANCE and z_diff <= self.TOLERANCE:
            logger.debug('No move needed.')
            return

        logger.debug(
            f'Direct simultaneous move -> R:{position.r():.1f} '
            f'T:{position.t():.1f}° Z:{position.z():.1f}'
        )
        self._scanner.move_to(position.r(), position.t(), position.z())

    def _perform_evasive_move(self, position: CylindricalPosition) -> None:
        """
        Safely route the arm around the outside of the measurement cylinder.

        Used for the interior-crossing transition (end of top cap -> start of
        next sector's bottom cap). The vertical sweep and the rotation are done
        while parked at ``safe_radius`` (outside the grid), so the arm never
        enters the protected interior.

        :param position: The target CylindricalPosition.
        """
        current_position = self._scanner.get_position()

        # Step 1: retract to the safe radius (outside the grid) if not already there.
        if current_position.r() < self._safe_radius - self.TOLERANCE:
            logger.debug(f'Evasive step 1: retract to safe radius R->{self._safe_radius:.1f}')
            self._scanner.radial_move_to(self._safe_radius)

        # Step 2: rotate and change Z simultaneously while parked outside the grid.
        logger.debug(
            f'Evasive step 2: slew at safe radius T->{position.t():.1f}° Z->{position.z():.1f}'
        )
        self._scanner.move_to(self._safe_radius, position.t(), position.z())

        # Step 3: move radially inward to the target radius at the target Z/theta.
        if abs(position.r() - self._safe_radius) > self.TOLERANCE:
            logger.debug(f'Evasive step 3: move in to target radius R->{position.r():.1f}')
            self._scanner.radial_move_to(position.r())


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
        """
        Initialize the spherical motion manager.

        :param scanner: The scanner instance to control.
        :param measurement_points: The set of points to measure.
        """
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
        """
        Check if all points have been measured.

        :return: True if ready (all points done), False otherwise.
        """
        return self._measurement_points.ready()

    def reset(self):
        """
        Reset the point sequence.
        """
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

    def total_points(self) -> int:
        """
        Get total number of points in the set.

        :return: Total points.
        """
        return self._measurement_points.total_points()

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


from nfs import registry

class MotionManagerFactory:
    """
    Factory for creating MotionManager instances.
    """
    @staticmethod
    def create(config_file: str, section: str, scanner: Scanner) -> IMotionManager:
        """
        Create a motion manager based on the configuration.

        :param config_file: Path to the configuration file.
        :param section: The configuration section to use.
        :param scanner: The scanner instance to use.
        :return: An instance of IMotionManager.
        """
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        # Try to get measurement points config from a referenced section OR directly from the current section
        measurement_points_section_name = config_parser.get(section, 'measurement_points', fallback=None)
        
        if measurement_points_section_name and config_parser.has_section(measurement_points_section_name):
            item = dict(config_parser.items(measurement_points_section_name))
        else:
            # If no reference or referenced section doesn't exist, use the current section
            item = dict(config_parser.items(section))
            # Remove keys that are specific to the motion manager itself to avoid passing them to measurement points
            item.pop('type', None)
            item.pop('safe_radius', None)
        
        measurement_points = factory.create(item)

        motion_manager_type = config_parser.get(section, 'type')

        # Check registry first for custom motion managers
        try:
            component = registry.motion_managers.get(motion_manager_type)
            return component(config_file, section, scanner, measurement_points)
        except (ValueError, TypeError):
            pass

        if motion_manager_type == 'CylindricalMeasurementMotionManager':
            safe_radius_raw = config_parser.get(section, 'safe_radius', fallback='').strip()
            if safe_radius_raw and safe_radius_raw.lower() != 'none':
                safe_radius = float(safe_radius_raw)
            else:
                safe_radius = 0.0
                logger.info(
                    f"No safe_radius configured for [{section}]; defaulting to 0.0mm"
                )
            return CylindricalMeasurementMotionManager(scanner, measurement_points, safe_radius)
        elif motion_manager_type == 'FastCylindricalMeasurementMotionManager':
            safe_radius_raw = config_parser.get(section, 'safe_radius', fallback='').strip()
            if safe_radius_raw and safe_radius_raw.lower() != 'none':
                safe_radius = float(safe_radius_raw)
            else:
                safe_radius = 0.0
                logger.info(
                    f"No safe_radius configured for [{section}]; defaulting to 0.0mm"
                )
            return FastCylindricalMeasurementMotionManager(scanner, measurement_points, safe_radius)
        elif motion_manager_type == 'SphericalMeasurementMotionManager':
            return SphericalMeasurementMotionManager(scanner, measurement_points)
        else:
            raise ValueError(f'Unknown motion manager type: {motion_manager_type}')
