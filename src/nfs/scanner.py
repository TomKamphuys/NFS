import configparser
import math
from abc import abstractmethod, ABC

from .datatypes import CylindricalPosition
from .grbl_controller import GrblControllerFactory, IGrblController
from .rotator import RotatorFactory, IRotator, GrblRotator


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
    :type _feed_rate: Float
    """
    def __init__(self, grbl_controller: IGrblController, feed_rate: float):
        self._feed_rate = feed_rate
        self._grbl_controller = grbl_controller

    def cw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        self._grbl_controller.send_and_wait_for_move_ready(f'G02 X{z:.4f} Y{r:.4f} R{radius:.4f} F{self._feed_rate}')

    def ccw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        self._grbl_controller.send_and_wait_for_move_ready(f'G03 X{z:.4f} Y{r:.4f} R{radius:.4f} F{self._feed_rate}')

    def move_to_rz(self, r: float, z: float) -> None:
        self._grbl_controller.send_and_wait_for_move_ready(f'G0 X{z:.4f} Y{r:.4f}')

    def move_to_vertical(self, z: float) -> None:
        self._grbl_controller.send_and_wait_for_move_ready(f'G0 X{z:.4f}')

    def move_to_radial(self, r: float) -> None:
        self._grbl_controller.send_and_wait_for_move_ready(f'G0 Y{r:.4f}')

    def set_as_zero(self) -> None:
        self._grbl_controller.send('G92 X0 Y0 Z0')
        self._grbl_controller.send('$10=0')

    def shutdown(self) -> None:
        self._grbl_controller.shutdown()

    def softreset(self) -> None:
        self._grbl_controller.softreset()

    def home(self) -> None:
        self._grbl_controller.send('$H')


class IScanner(ABC):
    """
    Abstract base class for Scanner implementations.
    Defines interface for controlling movement in cylindrical coordinates (radial, angular, vertical).
    """

    @abstractmethod
    def radial_move_to(self, r: float) -> None:
        """Move to the specified radial position."""
        pass

    @abstractmethod
    def planar_move_to(self, r: float, z: float) -> None:
        """Move to a specified planar position (radial and vertical)."""
        pass

    @abstractmethod
    def cw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        """Move in a clockwise arc to the specified radial and vertical position."""
        pass

    @abstractmethod
    def ccw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        """Move in a counter-clockwise arc to the specified radial and vertical position."""
        pass

    @abstractmethod
    def angular_move_to(self, angle: float) -> None:
        """Rotate to a specified angular position."""
        pass

    @abstractmethod
    def vertical_move_to(self, z: float) -> None:
        """Move to a specified vertical position."""
        pass

    @abstractmethod
    def rotate_ccw(self, amount: float) -> None:
        """Rotate counterclockwise by a specified amount."""
        pass

    @abstractmethod
    def rotate_cw(self, amount: float) -> None:
        """Rotate clockwise by a specified amount."""
        pass

    @abstractmethod
    def move_out(self, amount: float) -> None:
        """Increase radial distance by a specified amount."""
        pass

    @abstractmethod
    def move_in(self, amount: float) -> None:
        """Decrease radial distance by a specified amount."""
        pass

    @abstractmethod
    def move_up(self, amount: float) -> None:
        """Increase the vertical position by a specified amount."""
        pass

    @abstractmethod
    def move_down(self, amount: float) -> None:
        """Decrease the vertical position by a specified amount."""
        pass

    @abstractmethod
    def get_position(self) -> CylindricalPosition:
        """Return the current cylindrical position."""
        pass

    @abstractmethod
    def set_as_zero(self) -> None:
        """Reset scanner to the zero position."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Shutdown the scanner's movers."""
        pass

    @abstractmethod
    def softreset(self) -> None:
        pass

    @abstractmethod
    def home(self) -> None:
        pass


class Scanner(IScanner):
    """
    Controls the movement of a scanner in cylindrical coordinates (radial, angular, vertical).
    Combines planar and angular movement capabilities and manages scanner's position.
    """
    ZERO_POSITION = (0, 0, 0)  # Constant for zeroed coordinates (r, theta, z)  # TODO quick hack, should be 0

    def __init__(self, planar_mover: PlanarMover, angular_mover: IRotator):
        self._planar_mover = planar_mover
        self._angular_mover = angular_mover
        self._cylindrical_position = CylindricalPosition(*self.ZERO_POSITION)
        self.set_as_zero()

    def radial_move_to(self, r: float) -> None:
        """Move to the specified radial position."""
        if self.get_position().r() != r:
            self._planar_mover.move_to_radial(r)
        self._update_position(r=r)

    def planar_move_to(self, r: float, z: float):
        """Move to a specified planar position (radial and vertical)."""
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
        """Increase the vertical position by a specified amount."""
        self.vertical_move_to(self._cylindrical_position.z() + amount)

    def move_down(self, amount: float) -> None:
        """Decrease the vertical position by a specified amount."""
        self.move_up(-amount)

    def get_position(self) -> CylindricalPosition:
        """Return the current cylindrical position."""
        return self._cylindrical_position

    def set_as_zero(self) -> None:
        """Reset scanner to the zero position."""
        self._planar_mover.set_as_zero()
        self._angular_mover.set_as_zero()
        self._update_position(*self.ZERO_POSITION)

    def shutdown(self) -> None:
        """Shutdown the scanner's movers."""
        self._planar_mover.shutdown()
        self._angular_mover.shutdown()

    def softreset(self) -> None:
        self._planar_mover.softreset()
        self._angular_mover.softreset()

    def home(self) -> None:
        self._planar_mover.home()
        # No homing of rotator as that is currently the same controller. So rotator is also homed.

    def _update_position(self, r: float = None, t: float = None, z: float = None) -> None:
        """Update the specified cylindrical position components."""
        if r is not None:
            self._cylindrical_position.set_r(r)
        if t is not None:
            self._cylindrical_position.set_t(t)
        if z is not None:
            self._cylindrical_position.set_z(z)


class ControllerFactory:
    @staticmethod
    def create(section: str, config_file: str) -> tuple[PlanarMover, IRotator]:
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        controller_type = config_parser.get(section, 'type')
        if controller_type == 'DualController':
            controller_section = config_parser.get(section, 'planar_mover_controller')
            grbl_controller = GrblControllerFactory.create(controller_section, config_file)
            feed_rate = config_parser.getfloat(section, 'feed_rate')
            planar_mover = PlanarMover(grbl_controller, feed_rate)

            rotation_controller = config_parser.get(section, 'rotator')
            angular_mover = RotatorFactory.create(rotation_controller, config_file)
            return planar_mover, angular_mover
        elif controller_type == 'CombinedController':
            grbl_section = config_parser.get(section, 'controller')
            feed_rate = config_parser.getfloat(section, 'feed_rate')

            grbl_controller = GrblControllerFactory.create(grbl_section, config_file)
            planar_mover = PlanarMover(grbl_controller, feed_rate)
            angular_mover = GrblRotator(grbl_controller, 'Z')
            return planar_mover, angular_mover
        else:
            raise ValueError(f'Unknown controller_type type: {controller_type}')


class ScannerFactory:
    """
    Factory for creating Scanner objects.

    This class provides a mechanism to create and configure a Scanner object
    using a configuration file. It reads the configuration file, initializes
    the necessary parts such as angular and planar movers, and builds the
    Scanner object.

    :ivar config_file: The path to the configuration file used to initialize
        the Scanner.
    :type config_file: Str
    """
    @staticmethod
    def create(config_file: str) -> Scanner:

        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)
        section = 'scanner'

        controller_section = config_parser.get(section, 'controller')
        planar_mover, angular_mover = ControllerFactory.create(controller_section, config_file)

        scanner = Scanner(planar_mover, angular_mover)

        return scanner
