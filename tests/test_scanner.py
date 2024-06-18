import configparser

import pytest
from loguru import logger
from scanner import Scanner, CylindricalPosition, is_between, is_vertical_move_safe, is_radial_move_safe
from nfs import NearFieldScanner
import factory
import loader
import matplotlib.pyplot as plt  # type: ignore
import numpy as np

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

    def need_to_do_evasive_move(self):
        return False

    def ready(self):
        if self._index > 10:
            return True
        else:
            return False


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


def test_measurement_points():
    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read('../config.ini')

    items = config_parser.items('plugins')
    _, plugins = zip(*items)

    # load the plugins
    loader.load_plugins(plugins)

    item = dict(config_parser.items('measurement_points'))
    measurement_points = factory.create(item)

    fig = plt.figure()
    ax = fig.add_subplot(projection='3d')

    xs = np.empty((0, 0))
    ys = np.empty((0, 0))
    zs = np.empty((0, 0))

    index = 0
    while not measurement_points.ready():
        index += 1
        point = measurement_points.next()
        logger.trace(f'{index} : {point}')
        r = point.r()
        t = point.t() / 180 * np.pi
        xs = np.append(xs, r * np.cos(t))
        ys = np.append(ys, r * np.sin(t))
        zs = np.append(zs, point.z())

    ax.scatter(xs, ys, zs)
    ax.set_aspect('equal', 'box')

    plt.show()


def test_take_measurements_set():
    radial_mover = GrblAxisMock()
    angular_mover = TicAxisMock()
    vertical_mover = GrblAxisMock()
    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read('../config.ini')
    evasive_move_radius = config_parser.getfloat('scanner', 'evasive_move_radius')

    scanner = Scanner(radial_mover, angular_mover, vertical_mover, evasive_move_radius)

    items = config_parser.items('plugins')
    _, plugins = zip(*items)

    # load the plugins
    loader.load_plugins(plugins)

    item = dict(config_parser.items('measurement_points'))
    measurement_points = factory.create(item)
    nfs = NearFieldScanner(scanner, AudioMock(), measurement_points)
    nfs.take_measurement_set()


def test_plugin():
    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read('../config.ini')

    items = config_parser.items('plugins')
    _, plugins = zip(*items)

    # load the plugins
    loader.load_plugins(plugins)

    item = dict(config_parser.items('measurement_points'))
    measurement_points = factory.create(item)
    index = 0
    while not measurement_points.ready():
        index += 1
        point = measurement_points.next()
        logger.trace(f'{index} : {point}')


def test_is_between():
    assert is_between(1, 2, 3)
    assert is_between(3, 2, 1)
    assert is_between(1, 1, 1)
    assert is_between(1, 1, 2)
    assert is_between(1, 2, 2)
    assert not is_between(1, 4, 3)
    assert not is_between(3, 4, 1)


def test_check_vertical_move():
    assert not is_vertical_move_safe(CylindricalPosition(0, 0, 375), 0, 375 / 2)

    assert not is_vertical_move_safe(CylindricalPosition(0, 0, -375), 0, -375 / 2)

    assert is_vertical_move_safe(CylindricalPosition(0, 0, 375), 200, 375 / 2)
    assert is_vertical_move_safe(CylindricalPosition(0, 0, -375), -200, -375 / 2)
