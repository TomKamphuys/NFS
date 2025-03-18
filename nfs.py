import configparser
from scanner import ScannerFactory, Scanner
from scanner import SphericalMeasurementMotionManager
from audio import AudioFactory, IAudio
import factory
import loader


class NearFieldScanner:
    """
    Represents a near-field scanner for audio and measurement management.

    This class facilitates the process of measuring impulse responses using the
    provided scanner, audio system, and motion manager. It supports taking single
    or multiple measurements and managing the scanner's lifecycle.

    :ivar _scanner: The scanner instance used for positioning and scanning.
    :type _scanner: Any
    :ivar _audio: The audio system used for measuring impulse responses.
    :type _audio: Any
    :ivar _measurement_motion_manager: The motion manager handling measurement
        positions.
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
            position = self._measurement_motion_manager.next()
            if self._measurement_motion_manager.ready():
                break

            self._audio.measure_ir(position)

        self._scanner.shutdown()

    def shutdown(self) -> None:
        self._scanner.shutdown()  # turn off stuff and tidy


class NearFieldScannerFactory:
    """
    Factory class for creating instances of NearFieldScanner.

    This factory utilizes configuration provided in a configuration file to
    load plugins, construct and initialize the scanner, audio components, and
    measurement points. The constructed NearFieldScanner instance is
    returned, allowing the user to perform near-field scanning operations.

    The factory ensures that all necessary dependencies are loaded and
    initialized correctly, providing seamless integration between the scanner,
    audio, and measurement modules.
    """
    @staticmethod
    def create(config_file: str) -> NearFieldScanner:
        """
        Create a Near Field Scanner based on config file
        :param config_file:
        :return: near field scanner
        """

        loader.load_plugins(config_file)

        scanner = ScannerFactory.create(config_file)
        audio = AudioFactory.create(config_file)

        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)
        item = dict(config_parser.items('measurement_points'))
        measurement_points = factory.create(item)

        measurement_manager = SphericalMeasurementMotionManager(scanner, measurement_points)

        return NearFieldScanner(scanner, audio, measurement_manager)
