from scanner import Scanner, TicFactory, TicAxis, Grbl, GrblXAxis, GrblYAxis, CylindricalPosition
from loguru import logger
from grbl_streamer_mock import GrblStreamerMock
from nfs import NearFieldScanner

# logger.remove(0)
logger.add('scanner.log', mode='w', level="TRACE")


def my_callback(eventstring, *data):
    args = []
    for d in data:
        args.append(str(d))
    logger.info("MY CALLBACK: event={} data={}".format(eventstring.ljust(30),
                                                       ", ".join(args)))


def test_scanner_can_move_in():
    grbl_streamer = GrblStreamerMock()

    grbl = Grbl(grbl_streamer)
    radial_mover = GrblXAxis(grbl)
    angular_mover = TicFactory().create('../config.ini')
    vertical_mover = GrblYAxis(grbl)
    scanner = Scanner(radial_mover, angular_mover, vertical_mover)
    cylindrical_position = CylindricalPosition(10, 0, 0)
    scanner.radial_move_to(cylindrical_position)

    # assert scanner.get_position() == cylindrical_position


def test_audio_capture():
    nfs = NearFieldScanner(42)
    nfs.take_single_measurement()
