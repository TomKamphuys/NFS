import configparser
import time

# import pytest
from loguru import logger
from datatypes import CylindricalPosition
from scanner import Scanner, is_between, is_vertical_move_safe, TicAxisMock, Grbl
from nfs import NearFieldScanner, NearFieldScannerFactory
from audio import AudioMock
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


class GrblAxisMock:
    def cw_arc_move_to(self, x, y, r):
        pass

    def ccw_arc_move_to(self, x, y, r):
        pass

    def move_to_rz(self, x: float, y: float) -> None:
        pass

    def move_to(self, bla: float) -> None:
        pass


class MeasurementPointsMock:
    def __init__(self):
        self._index = 0

    def next(self):
        self._index += 1
        return CylindricalPosition(self._index, self._index, self._index)

    def move_to_safe_starting_position(self):
        pass

    def ready(self) -> bool:
        return self._index > 10


def test_grbl():
    grbl = Grbl('config.ini')

    grbl.move_x_to(100)
    grbl.move_y_to(100)
    grbl.move_z_to(100)
    grbl.move_to(10, 10)
    grbl.move_to(0, 100)
    grbl.cw_arc_move_to(100, 0, 100)
    grbl.ccw_arc_move_to(0, 100, 100)
    grbl.get_current_position()
    grbl.send('?')
    time.sleep(2)
    grbl.send_and_wait('G0X0Y0')
    assert True

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
    config_file = 'config.ini'

    # load the plugins
    loader.load_plugins(config_file)

    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read(config_file)
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
    config_file = 'config.ini'

    # load the plugins
    loader.load_plugins(config_file)

    nfs = NearFieldScannerFactory.create(config_file)

    nfs.take_measurement_set()


def test_plugin():
    config_file = 'config.ini'

    # load the plugins
    loader.load_plugins(config_file)

    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read(config_file)

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
