import configparser

from loguru import logger
from scanner import Scanner, CylindricalPosition
from nfs import NearFieldScanner

logger.remove(0)
logger.add('scanner.log', mode='w', level="TRACE", backtrace=True, diagnose=True)


def my_callback(event_string, *data):
    args = []
    for d in data:
        args.append(str(d))
    logger.info("MY CALLBACK: event={} data={}".format(event_string.ljust(30),
                                                       ", ".join(args)))


class ScannerMock:
    @staticmethod
    def get_position():
        return CylindricalPosition(0, 0, 0)

    @staticmethod
    def move_to(position):
        logger.debug(f'Moving to: {position}')


class TicAxisMock:
    def move_to(self, position):
        pass

    @staticmethod
    def get_position():
        return CylindricalPosition(0, 0, 0)


class GrblAxisMock:
    def move_to(self, position):
        pass


class AudioMock:
    @staticmethod
    def measure_ir(position):
        logger.debug(f'IR measurement for position {position}')


class MeasurementPointsMock:
    def __init__(self):
        self._index = 0

    def next(self):
        self._index += 1
        return CylindricalPosition(self._index, self._index, self._index)


def test_scanner_can_move_in():
    radial_mover = GrblAxisMock()
    angular_mover = TicAxisMock()
    vertical_mover = GrblAxisMock()
    scanner = Scanner(radial_mover, angular_mover, vertical_mover, 300)
    scanner.radial_move_to(10)

    # assert scanner.get_position() == cylindrical_position


def test_single_measurement():
    nfs = NearFieldScanner(ScannerMock(), AudioMock(), MeasurementPointsMock())
    nfs.take_single_measurement()


def test_take_measurement_set():
    radial_mover = GrblAxisMock()
    angular_mover = TicAxisMock()
    vertical_mover = GrblAxisMock()
    scanner = Scanner(radial_mover, angular_mover, vertical_mover, 300)
    nfs = NearFieldScanner(scanner, AudioMock(), MeasurementPointsMock())
    nfs.take_measurement_set()


# def test_measurement_points():
#     measurement_points = MeasurementPointsFactory().create('../config.ini')
#     index = 0
#     while not measurement_points.ready():
#         index += 1
#         point = measurement_points.next()
#         logger.trace(f'{index} : {point}')


def test_take_measurements_set():
    radial_mover = GrblAxisMock()
    angular_mover = TicAxisMock()
    vertical_mover = GrblAxisMock()
    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read('../config.ini')
    evasive_move_radius = config_parser.getfloat('scanner', 'evasive_move_radius')

    scanner = Scanner(radial_mover, angular_mover, vertical_mover, evasive_move_radius)

    plugins = config_parser.get('plugins', 'nr1')

    # load the plugins
    loader.load_plugins([plugins])

    item = dict(config_parser.items('measurement_points'))
    measurement_points = factory.create(item)
    nfs = NearFieldScanner(scanner, AudioMock(), measurement_points)
    nfs.take_measurement_set()


import factory
import loader


def test_plugin():
    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read('../config.ini')

    plugins = config_parser.get('plugins', 'nr1')

    # load the plugins
    loader.load_plugins([plugins])

    item = dict(config_parser.items('measurement_points'))
    measurement_points = factory.create(item)
    index = 0
    while not measurement_points.ready():
        index += 1
        point = measurement_points.next()
        logger.trace(f'{index} : {point}')