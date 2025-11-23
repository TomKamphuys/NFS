import time

from loguru import logger

from . import loader
from .audio import AudioFactory, IAudio
from .scanner import Scanner
from .scanner import MotionManagerFactory, IMotionManager

logger.add('../../scanner.log', mode='w', level="TRACE")


class NearFieldScanner:
    """
    The NearFieldScanner class is responsible for taking single and multiple
    acoustic measurements using a scanner and an audio interface. It interacts
    with a motion manager to handle positioning for a series of measurements
    and ensures safe and proper operation of the hardware.

    :ivar _scanner: The scanner object used to manage the scanning hardware.
    :type _scanner: Scanner
    :ivar _audio: The audio interface for measurement and signal capture.
    :type _audio: IAudio
    :ivar _measurement_motion_manager: Manager responsible for controlling motions
        during measurements and handling safe transitions.
    :type _measurement_motion_manager: Any
    :ivar _position_log_file: Path to the file where measurement positions are logged.
    :type _position_log_file: str
    """
    def __init__(self, scanner: Scanner, audio: IAudio, measurement_motion_manager, position_log_file: str = 'measurement_positions.txt'):
        self._scanner = scanner
        self._audio = audio
        self._measurement_motion_manager = measurement_motion_manager
        self._position_log_file = position_log_file
        self._clear_position_log()

    def _clear_position_log(self) -> None:
        """
        Clears the position log file at initialization and writes CSV header.
        :return: None
        """
        with open(self._position_log_file, 'w') as f:
            f.write('r,theta,z\n')
        logger.info(f'Position log file cleared: {self._position_log_file}')

    def _append_position_to_file(self, position) -> None:
        """
        Appends the measurement position to the log file as CSV with numeric values only.
        :param position: The position to be logged (CylindricalPosition)
        :return: None
        """
        with open(self._position_log_file, 'a') as f:
            f.write(f'{position.r()},{position.t()},{position.z()}\n')
        logger.debug(f'Position logged: {position}')

    def take_single_measurement(self) -> None:
        """
        This function takes a single measurement. This is handy for checking
        the audio levels.
        :return: Nothing
        """
        self._audio.measure_ir(self._scanner.get_position())

    def take_measurement_set(self) -> None:
        """
        Take a full set of measurements.
        :return: nothing
        """
        self._measurement_motion_manager.move_to_safe_starting_radius()
        while not self._measurement_motion_manager.ready():
            time.sleep(0.1)
            self._measurement_motion_manager.next()
            if self._measurement_motion_manager.ready():
                break

            position = self._scanner.get_position()
            self._append_position_to_file(position)
            self._audio.measure_ir(position)
            

        self._measurement_motion_manager.reset()
        self._measurement_motion_manager.move_to_safe_starting_radius()
        self._scanner.angular_move_to(0.0)


    def shutdown(self) -> None:
        """
        Shuts down the scanner system gracefully.

        This method shuts down the scanner system by invoking the shutdown
        functionality of the internal scanner component. It ensures that all
        internal operations are stopped and resources are tidied up properly.

        :return: None
        """
        self._scanner.shutdown()  # turn off stuff and tidy


class NearFieldScannerFactory:
    """
    A factory class for creating Near Field Scanner objects.

    This class provides a method to create a Near Field Scanner by using
    the given scanner and a configuration file. It handles loading plugins,
    creating necessary audio configurations, parsing the configuration file,
    and initializing the measurement manager for the scanner.
    """
    @staticmethod
    def create(scanner: Scanner, config_file: str) -> NearFieldScanner:
        """
        Create a Near Field Scanner based on a config file
        :param scanner:
        :param config_file:
        :return: near field scanner
        """

        loader.load_plugins(config_file)

        audio = AudioFactory.create(config_file)

        measurement_manager = MotionManagerFactory.create(config_file, scanner)

        return NearFieldScanner(scanner, audio, measurement_manager)
