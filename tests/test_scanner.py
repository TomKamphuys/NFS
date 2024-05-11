import configparser

from loguru import logger

logger.add('scanner.log', mode='w', level="TRACE", backtrace=True, diagnose=True)

from scanner import Scanner, TicFactory, TicAxis, Grbl, GrblXAxis, GrblYAxis, CylindricalPosition
from grbl_streamer_mock import GrblStreamerMock
from nfs import NearFieldScanner, MeasurementPointsFactory
import numpy as np

logger.remove(0)


def my_callback(eventstring, *data):
    args = []
    for d in data:
        args.append(str(d))
    logger.info("MY CALLBACK: event={} data={}".format(eventstring.ljust(30),
                                                       ", ".join(args)))


class ScannerMock:
    def get_position(self):
        return CylindricalPosition(0, 0, 0)

    def move_to(self, position):
        logger.debug(f'Moving to: {position}')


class TicAxisMock:
    def move_to(self, position):
        pass

    def get_position(self):
        return CylindricalPosition(0, 0, 0)


class GrblAxisMock:
    def move_to(self, position):
        pass


class AudioMock:
    def measure_ir(self, position):
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
    scanner = Scanner(radial_mover, angular_mover, vertical_mover)
    cylindrical_position = CylindricalPosition(10, 0, 0)
    scanner.radial_move_to(cylindrical_position)

    # assert scanner.get_position() == cylindrical_position


def test_single_measurement():
    nfs = NearFieldScanner(ScannerMock(), AudioMock(), MeasurementPointsMock())
    nfs.take_single_measurement()


def test_take_measurement_set():
    radial_mover = GrblAxisMock()
    angular_mover = TicAxisMock()
    vertical_mover = GrblAxisMock()
    scanner = Scanner(radial_mover, angular_mover, vertical_mover)
    nfs = NearFieldScanner(scanner, AudioMock(), MeasurementPointsMock())
    nfs.take_measurement_set()


def test_measurement_points():
    measurement_points = MeasurementPointsFactory().create('../config.ini')
    index = 0
    while not measurement_points.ready():
        index += 1
        point = measurement_points.next()
        logger.trace(f'{index} : {point}')


def test_take_measurements_set():
    radial_mover = GrblAxisMock()
    angular_mover = TicAxisMock()
    vertical_mover = GrblAxisMock()
    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read('../config.ini')
    evasive_move_radius = config_parser.getfloat('scanner', 'evasive_move_radius')

    scanner = Scanner(radial_mover, angular_mover, vertical_mover, evasive_move_radius)
    nfs = NearFieldScanner(scanner, AudioMock(), MeasurementPointsFactory().create('../config.ini'))
    nfs.take_measurement_set()
