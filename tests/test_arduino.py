# import pytest
import time
import os

from grbl_streamer import GrblStreamer
from loguru import logger
from nfs.grbl_controller import Arduino, GrblControllerFactory

logger.add('arduino.log', mode='w', level="TRACE")

def on_grbl_event(event, *data) -> None:
    logger.trace(event)
    args = []
    for d in data:
        args.append(str(d))
    logger.trace("MY CALLBACK: event={} data={}".format(event.ljust(30), ", ".join(args)))


def test_send_message():
    port = 'COM9'
    baudrate = 115200
    # arduino = Arduino()
    grbl_streamer = GrblStreamer(on_grbl_event)

    grbl_streamer.setup_logging()
    grbl_streamer.cnect(port, baudrate)
    logger.info('Waiting for gbrl to initialize..')
    time.sleep(3)
    grbl_streamer.poll_start()
    grbl_streamer.incremental_streaming = True

    grbl_streamer.send_immediately('$$')

def test_arduino():
    arduino = GrblControllerFactory.create('grbl_streamer', 'tests/config.ini')
    logger.info('BLA1')
    arduino.send_and_wait_for_move_ready('G0 X10')
    logger.info('BLA2')
    arduino.send('$$')
    logger.info('BLA3')
    arduino.shutdown()
