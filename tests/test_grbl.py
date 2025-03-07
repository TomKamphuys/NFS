from grbl_controller import GrblControllerMock
from scanner import Grbl, PlanarMover
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
    grbl_controller = GrblControllerMock();
    grbl = Grbl(grbl_controller, 15000)  # (grbl_streamer)
    mover = PlanarMover(grbl)

    mover.set_as_zero()
    mover.move_to_rz(10, 0)
    mover.cw_arc_move_to(-10, 0, 10)