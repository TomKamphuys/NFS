import pyfar as pf
import numpy as np
import sounddevice as sd  # type: ignore
import os
from loguru import logger
import configparser
import math
from datatypes import CylindricalPosition

logger.add('scanner.log', mode='w', level="TRACE")


class Audio:
    def __init__(self, device_id, sample_rate, minimum_frequency, maximum_frequency, duration, padding_time):
        self._sample_rate = sample_rate
        self._minimum_frequency = minimum_frequency
        self._maximum_frequency = maximum_frequency
        self._duration = duration
        self._padding_time = padding_time
        sd.default.device = device_id

    def measure_ir(self, position: CylindricalPosition) -> None:
        logger.info(f'IR measurement for position {position}')
        x = pf.signals.exponential_sweep_time(
            n_samples=self._duration * self._sample_rate,
            frequency_range=[self._minimum_frequency, self._maximum_frequency],
            sampling_rate=self._sample_rate)
        x_padded = pf.dsp.pad_zeros(x, pad_width=math.floor(self._padding_time * x.sampling_rate))

        recording = sd.playrec(
            np.concatenate((x_padded.time.T, x_padded.time.T), axis=1), x_padded.sampling_rate, channels=2,
            blocking=True)

        pf.io.write_audio(
            pf.Signal(recording.T, x_padded.sampling_rate),
            os.path.join('Recordings', 'raw', f'{position}.wav'),
            'DOUBLE')

        y = pf.Signal(recording[:, 0].T, x_padded.sampling_rate)
        x_reference = pf.Signal(recording[:, 1].T, x_padded.sampling_rate)

        x_inverted = pf.dsp.regularized_spectrum_inversion(x_reference,
                                                           (self._minimum_frequency, self._maximum_frequency))
        h = y * x_inverted

        # apply high-pass to reject out of band noise
        h_processed = pf.dsp.filter.butterworth(h, 8, 15, 'highpass')

        # window to reduce impulse response length
        h_processed = pf.dsp.time_window(h_processed, [0, 0.1], window='boxcar', unit='s', crop='window')

        pf.io.write_audio(
            h_processed,
            os.path.join('Recordings', f'{position}.wav'),
            'DOUBLE')


class AudioMock:
    def __init__(self):
        pass

    def measure_ir(self, position: CylindricalPosition) -> None:
        logger.trace(f'{position}')

class AudioFactory:
    @staticmethod
    def create(config_file: str) -> Audio:
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        section = 'audio'
        device_id = config_parser.getint(section, 'device_id')
        sample_rate = config_parser.getfloat(section, 'sample_rate')
        minimum_frequency = config_parser.getfloat(section, 'minimum_frequency')
        maximum_frequency = config_parser.getfloat(section, 'maximum_frequency')
        duration = config_parser.getfloat(section, 'duration')
        padding_time = config_parser.getfloat(section, 'padding_time')
        return Audio(device_id, sample_rate, minimum_frequency, maximum_frequency, duration, padding_time)
