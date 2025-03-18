import configparser

import pytest
from loguru import logger
from datatypes import CylindricalPosition
from nfs import NearFieldScanner, NearFieldScannerFactory
from audio import AudioMock
import factory
import loader
import matplotlib.pyplot as plt  # type: ignore
import numpy as np

# logger.remove(0)
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


@pytest.mark.skip(reason="This is an interactive test I use manually")
def test_single_measurement():
    nfs = NearFieldScanner(ScannerMock(), AudioMock(), MeasurementPointsMock())
    nfs.take_single_measurement()


@pytest.mark.skip(reason="This is an interactive test I use manually")
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


@pytest.mark.skip(reason="This is an interactive test I use manually")
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
