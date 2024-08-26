# import pytest
from loguru import logger
from audio import AudioFactory
from datatypes import CylindricalPosition

logger.remove(0)
logger.add('audio.log', mode='w', level="TRACE", backtrace=True, diagnose=True)

def test_measure_ir():
    audio = AudioFactory.create('../config.ini')
    audio.measure_ir(CylindricalPosition(0,0,0))
