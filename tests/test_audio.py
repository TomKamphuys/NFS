from loguru import logger
from audio import AudioFactory, Audio
from datatypes import CylindricalPosition

logger.remove(0)
logger.add('audio.log', mode='w', level="TRACE", backtrace=True, diagnose=True)


def test_measure_ir():
    audio = Audio();
    audio.measure_ir(CylindricalPosition(0,0,0))
    # don't know what/how to test

def test_audiofactory_create():
    audio = AudioFactory.create('../config.ini')
