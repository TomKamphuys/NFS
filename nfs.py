from scanner import ScannerFactory
import configparser
from audio import AudioFactory
import factory
import loader


class NearFieldScanner:
    def __init__(self, scanner, audio, measurement_points):
        self._scanner = scanner
        self._audio = audio
        self._measurement_points = measurement_points

    def take_single_measurement(self) -> None:
        self._audio.measure_ir(self._scanner.get_position())

    def take_measurement_set(self) -> None:
        while not self._measurement_points.ready():
            position = self._measurement_points.next()
            if self._measurement_points.ready():
                break

            if self._measurement_points.need_to_do_evasive_move():
                self._scanner.evasive_move_to(position)
            else:
                self._scanner.move_to(position)

            # time.sleep(3.0)  # TODO we should wait for move ready
            self._audio.measure_ir(position)

    def shutdown(self) -> None:
        pass  # turn off stuff and tidy


class NearFieldScannerFactory:
    @staticmethod
    def create(config_file: str) -> NearFieldScanner:
        scanner = ScannerFactory().create(config_file)
        audio = AudioFactory().create(config_file)

        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        plugins = config_parser.get('plugins', 'nr1')

        # load the plugins
        loader.load_plugins([plugins])

        item = dict(config_parser.items('measurement_points'))
        measurement_points = factory.create(item)


        return NearFieldScanner(scanner, audio, measurement_points)
