import configparser
from loguru import logger

from .datatypes import CylindricalPosition
from .grbl_controller import GrblControllerFactory, IGrblController


class Scanner:
    """
    Controls the movement of a scanner in cylindrical coordinates (radial, angular, vertical).
    Combines planar and angular movement capabilities and manages scanner's position.
    """
    ZERO_POSITION = (0, 0, 0)  # Constant for zeroed coordinates (r, theta, z)

    def __init__(self, grbl_controller: IGrblController, feed_rate):
        self._grbl_controller = grbl_controller
        self._stool_reference = None
        # self._secondary_reference = None
        self._height_offset = None
        self._cylindrical_position = CylindricalPosition(*self.ZERO_POSITION)
        self._feed_rate = feed_rate

    def _initialize(self) -> None:
        self._stool_reference = None
        # self._secondary_reference = None
        self._height_offset = None
        self._cylindrical_position = CylindricalPosition(*self.ZERO_POSITION)
        self.set_as_zero()

    def radial_move_to(self, r: float) -> None:
        """Move to the specified radial position."""
        if self.get_position().r() != r:
            self._grbl_controller.send_and_wait_for_move_ready(f'G0 Y{r:.4f}')

        self._update_position(r=r)

    def planar_move_to(self, r: float, z: float):
        """Move to a specified planar position (radial and vertical)."""
        self._grbl_controller.send_and_wait_for_move_ready(f'G0 X{z:.4f} Y{r:.4f}')
        self._update_position(r=r, z=z)

    def cw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        """Move in a clockwise arc to the specified radial and vertical position."""
        self._grbl_controller.send_and_wait_for_move_ready(f'G02 X{z:.4f} Y{r:.4f} R{radius:.4f} F{self._feed_rate}')
        self._update_position(r=r, z=z)

    def ccw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        """Move in a counter-clockwise arc to the specified radial and vertical position."""
        self._grbl_controller.send_and_wait_for_move_ready(f'G03 X{z:.4f} Y{r:.4f} R{radius:.4f} F{self._feed_rate}')
        self._update_position(r=r, z=z)

    def angular_move_to(self, angle: float) -> None:
        """Rotate to a specified angular position."""
        if self.get_position().t() != angle:
            logger.trace(f'Sending move-to command for {angle:.1f}°')
            self._grbl_controller.send_and_wait_for_move_ready(f'G0 Z{angle:.1f}')

        self._update_position(t=angle)

    def vertical_move_to(self, z: float) -> None:
        """Move to a specified vertical position."""
        if self.get_position().z() != z:
            self._grbl_controller.send_and_wait_for_move_ready(f'G0 X{z:.4f}')
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
        """Reset scanner Work Coordinate System."""
        self._set_as(CylindricalPosition(*self.ZERO_POSITION))

    def is_calibrated(self) -> bool:
        """Check if all zero positions and offsets have been set."""
        return all(v is not None for v in [self._stool_reference, self._height_offset])

    def set_working_coordinate_system(self) -> None:
        if self.is_calibrated():
            wcs_zero_in_mcs = self._stool_reference
            wcs_zero_in_mcs.set_z(wcs_zero_in_mcs.z() - self._height_offset)
            new_current_position = self._cylindrical_position - wcs_zero_in_mcs
            self._set_as(new_current_position)
            logger.info('WCS set')
        else:
            logger.warning('Scanner not calibrated!')

    def _set_as(self, position: CylindricalPosition) -> None:
        """Reset scanner Work Coordinate System."""
        x = position.z()
        y = position.r()
        z = position.t()
        logger.warning(f'Set as: G92 X{x} Y{y} Z{z}')
        self._grbl_controller.send(f'G92 X{x} Y{y} Z{z}')
        self._grbl_controller.send('$10=0')  # TODO MPOT Why is this needed? This sets the reporting format...
        self._update_position(y, z, x)

    def set_stool_reference(self) -> None:
        self._stool_reference = self._cylindrical_position

    # def set_secondary_reference(self) -> None:
    #     if self._stool_reference is None:
    #         logger.warning('No stool reference set! Cannot set secondary reference!')
    #         return
    #     self._secondary_reference = self._cylindrical_position

    def set_height_offset(self, height_offset: float) -> None:
        self._height_offset = height_offset # positive upwards

    def shutdown(self) -> None:
        """Shutdown the scanner's movers."""
        self._grbl_controller.shutdown()

    def home(self) -> None:
        self._grbl_controller.send('$H')
        self._initialize()
        self.rotate_cw(180.0)

    def clear_alarm(self) -> None:
        self._grbl_controller.killalarm()

    def softreset(self) -> None:
        self._grbl_controller.softreset();

    def _update_position(self, r: float = None, t: float = None, z: float = None) -> None:
        """Update the specified cylindrical position components."""
        if r is not None:
            self._cylindrical_position.set_r(r)
        if t is not None:
            self._cylindrical_position.set_t(t)
        if z is not None:
            self._cylindrical_position.set_z(z)


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

        grbl_section = config_parser.get(section, 'controller')
        grbl_controller = GrblControllerFactory.create(grbl_section, config_file)
        feed_rate = config_parser.getfloat(section, 'feed_rate')

        scanner = Scanner(grbl_controller, feed_rate)

        return scanner
