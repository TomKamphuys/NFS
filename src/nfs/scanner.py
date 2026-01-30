import configparser
from loguru import logger

from .datatypes import CylindricalPosition, GrblMachineState
from .grbl_controller import GrblControllerFactory, IGrblController


class Scanner:
    """
    Controls the movement of a scanner in cylindrical coordinates (radial, angular, vertical).
    Combines planar and angular movement capabilities and manages scanner's position.
    """
    def __init__(self, grbl_controller: IGrblController, feed_rate):
        self._grbl_controller = grbl_controller
        self._feed_rate = feed_rate

    def radial_move_to(self, r: float) -> None:
        """Move to the specified radial position."""
        if self.get_position().r() != r:
            self._grbl_controller.send_and_wait_for_move_ready(f'G0 Y{r:.4f}')

    def planar_move_to(self, r: float, z: float):
        """Move to a specified planar position (radial and vertical)."""
        self._grbl_controller.send_and_wait_for_move_ready(f'G0 X{z:.4f} Y{r:.4f}')

    def cw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        """Move in a clockwise arc to the specified radial and vertical position."""
        self._grbl_controller.send_and_wait_for_move_ready(f'G02 X{z:.4f} Y{r:.4f} R{radius:.4f} F{self._feed_rate}')

    def ccw_arc_move_to(self, r: float, z: float, radius: float) -> None:
        """Move in a counter-clockwise arc to the specified radial and vertical position."""
        self._grbl_controller.send_and_wait_for_move_ready(f'G03 X{z:.4f} Y{r:.4f} R{radius:.4f} F{self._feed_rate}')

    def angular_move_to(self, angle: float) -> None:
        """Rotate to a specified angular position."""
        if self.get_position().t() != angle:
            logger.trace(f'Sending move-to command for {angle:.1f}°')
            self._grbl_controller.send_and_wait_for_move_ready(f'G0 Z{angle:.1f}')

    def vertical_move_to(self, z: float) -> None:
        """Move to a specified vertical position."""
        if self.get_position().z() != z:
            self._grbl_controller.send_and_wait_for_move_ready(f'G0 X{z:.4f}')

    def rotate_ccw(self, amount: float) -> None:
        """Rotate counterclockwise by a specified amount."""
        self.angular_move_to(self.get_position().t() + amount)

    def rotate_cw(self, amount: float) -> None:
        """Rotate clockwise by a specified amount."""
        self.rotate_ccw(-amount)

    def move_out(self, amount: float) -> None:
        """Increase radial distance by a specified amount."""
        self.radial_move_to(self.get_position().r() + amount)

    def move_in(self, amount: float) -> None:
        """Decrease radial distance by a specified amount."""
        self.move_out(-amount)

    def move_up(self, amount: float) -> None:
        """Increase the vertical position by a specified amount."""
        self.vertical_move_to(self.get_position().z() + amount)

    def move_down(self, amount: float) -> None:
        """Decrease the vertical position by a specified amount."""
        self.move_up(-amount)

    def get_position(self) -> CylindricalPosition:
        """Return the current cylindrical position."""
        return self._grbl_controller.get_position()

    def get_state(self) -> GrblMachineState:
        """Return the current normalized GRBL state (Idle/Run/Alarm/...)."""
        return self._grbl_controller.get_state()

    def get_state_raw(self) -> str:
        """Return the raw mode string last reported by GRBL/FluidNC (e.g. 'Hold:0')."""
        return self._grbl_controller.get_state_raw()

    def is_idle(self) -> bool:
        return self.get_state() == GrblMachineState.IDLE

    def is_running(self) -> bool:
        return self.get_state() == GrblMachineState.RUN

    def is_alarm(self) -> bool:
        return self.get_state() == GrblMachineState.ALARM

    def set_as_zero(self) -> None:
        """Reset scanner Work Coordinate System (Persistent)."""
        # G10 L20 P2 sets the CURRENT position as the zero point for G55 (P2).
        # Unlike G92, this is saved to EEPROM and survives restarts.
        self._grbl_controller.send(f'G10 L20 P2 X0 Y0 Z0')

    def set_speaker_center_above_stool(self, height: float) -> None:
        """
        Sets G55 to be exactly 'height' above G54.
        Ensures Y (radial) and Z (angular) are identical to G54.
        """
        # 1. Force switch to G55 to ensure we read reference coordinates
        self._grbl_controller.send('G55')

        # 2. Sync and get current position in G54
        self._grbl_controller.send('G4 P0.1')
        current_pos = self.get_position()

        # Mapping: r->Y, t->Z, z->X (based on scanner.py move methods)
        g55_r = current_pos.r()  # Y axis
        g55_t = current_pos.t()  # Z axis
        g55_z = current_pos.z()  # X axis

        # 3. Calculate G54 values for the same physical point.
        # To shift the G54 origin UP, the coordinate value in G54
        # must be 'height' smaller than in G55.
        g54_x_val = g55_z - height
        g54_y_val = g55_r
        g54_z_val = g55_t

        # 4. Use G10 L20 P1 to align G54 to G55 with the vertical offset
        self._grbl_controller.send(f'G10 L20 P1 X{g54_x_val:.4f} Y{g54_y_val:.4f} Z{g54_z_val:.4f}')

        # 5. Be sure to go to G54
        self._grbl_controller.send('G54')

        logger.info(f"G54 aligned to G55 with +{height} vertical offset")

    def shutdown(self) -> None:
        """Shutdown the scanner's movers."""
        self._grbl_controller.shutdown()

    def home(self) -> None:
        self._grbl_controller.send_and_wait_for_move_ready('$H')

    def clear_alarm(self) -> None:
        self._grbl_controller.killalarm()

    def softreset(self) -> None:
        self._grbl_controller.softreset()


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
