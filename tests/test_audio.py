from loguru import logger

# from nfs.audio import Audio, ExponentialSweep
# from nfs.datatypes import CylindricalPosition
#
# logger.remove(0)
# logger.add('audio.log', mode='w', level="TRACE", backtrace=True, diagnose=True)
#
# def test_measure_ir():
#     sample_rate = 44100  # Hz
#     minimum_frequency = 20  # Hz
#     maximum_frequency = 20000  # Hz
#     duration = 1  # s
#     padding_time = 1  # s
#     sweep = ExponentialSweep(sample_rate, minimum_frequency, maximum_frequency, duration, padding_time)
#     audio = Audio(0, sweep.generate())
#     audio.measure_ir(CylindricalPosition(0,0,0))
    # don't know what/how to test
#
# def test_audiofactory_create():
#     audio = AudioFactory.create('../config.ini')
