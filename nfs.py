from scanner import ScannerFactory
from scanner import SphericalMeasurementMotionManager
import configparser
from audio import AudioFactory
import factory
import loader


class NearFieldScanner:
    def __init__(self, scanner, audio, measurement_motion_manager):
        self._scanner = scanner
        self._audio = audio
        self._measurement_motion_manager = measurement_motion_manager

    def take_single_measurement(self) -> None:
        self._audio.measure_ir(self._scanner.get_position())

    def take_measurement_set(self) -> None:
        self._measurement_motion_manager.move_to_safe_starting_position()
        while not self._measurement_motion_manager.ready():
            position = self._measurement_motion_manager.next()
            if self._measurement_motion_manager.ready():
                break

            self._audio.measure_ir(position)

    def shutdown(self) -> None:
        pass  # turn off stuff and tidy

    @property
    def scanner(self):
        return self._scanner


class NearFieldScannerFactory:
    @staticmethod
    def create(config_file: str) -> NearFieldScanner:

        loader.load_plugins(config_file)

        scanner = ScannerFactory().create(config_file)
        audio = AudioFactory().create(config_file)

        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)
        item = dict(config_parser.items('measurement_points'))
        measurement_points = factory.create(item)

        angular_mover = scanner._angular_mover
        plane_mover = scanner._radial_mover

        measurement_manager = SphericalMeasurementMotionManager(angular_mover, plane_mover, measurement_points)

        return NearFieldScanner(scanner, audio, measurement_manager)
