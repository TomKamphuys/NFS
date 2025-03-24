import configparser

import factory
import loader
from audio import AudioFactory, IAudio
from scanner import Scanner
from scanner import SphericalMeasurementMotionManager


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
    """
    def __init__(self, scanner: Scanner, audio: IAudio, measurement_motion_manager):
        self._scanner = scanner
        self._audio = audio
        self._measurement_motion_manager = measurement_motion_manager

    def take_single_measurement(self) -> None:
        """
            This function takes a single measurement. This is handy for checking
            the audio levels.
            :return: nothing
            """
        self._audio.measure_ir(self._scanner.get_position())

    def take_measurement_set(self) -> None:
        """
        Take a full set of measurements.
        :return: nothing
        """
        self._measurement_motion_manager.move_to_safe_starting_position()
        while not self._measurement_motion_manager.ready():
            self._measurement_motion_manager.next()
            if self._measurement_motion_manager.ready():
                break

            self._audio.measure_ir(self._scanner.get_position())

        self._scanner.shutdown()

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

    This class provides a method to create a Near Field Scanner by utilizing
    the given scanner and a configuration file. It handles loading plugins,
    creating necessary audio configurations, parsing the configuration file,
    and initializing the measurement manager for the scanner.
    """
    @staticmethod
    def create(scanner: Scanner, config_file: str) -> NearFieldScanner:
        """
        Create a Near Field Scanner based on config file
        :param scanner:
        :param config_file:
        :return: near field scanner
        """

        loader.load_plugins(config_file)

        audio = AudioFactory.create(config_file)

        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)
        item = dict(config_parser.items('measurement_points'))
        measurement_points = factory.create(item)

        measurement_manager = SphericalMeasurementMotionManager(scanner, measurement_points)

        return NearFieldScanner(scanner, audio, measurement_manager)
