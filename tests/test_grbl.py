import configparser

from setuptools.command.setopt import config_file

from scanner import Grbl, GrblAxis

# import pytest
from loguru import logger

logger.remove(0)
logger.add('grbl.log', mode='w', level="TRACE", backtrace=True, diagnose=True)

def my_callback(event_string, *data):
    args = []
    for d in data:
        args.append(str(d))
    logger.info("MY CALLBACK: event={} data={}".format(event_string.ljust(30),
                                                       ", ".join(args)))

def test_iets():
    grbl = Grbl('../config.ini')  # (grbl_streamer)
    mover = GrblAxis(grbl)

    mover.set_as_zero()
    mover.move_to_rz(10, 0)
    mover.cw_arc_move_to(-10, 0, 10)


def test_grbl():
    config_file = '../config.ini'
    grbl = Grbl(config_file)

    config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
    config_parser.read(config_file)

    grbl._set_axis_according_to_config(config_parser, 'x')