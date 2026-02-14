import configparser
import math
import os
from abc import ABC, abstractmethod
from typing import Union

import numpy as np
import pyfar as pf  # type: ignore
import sounddevice as sd  # type: ignore
from loguru import logger

from .datatypes import CylindricalPosition


class Sweep:
    def __init__(self, sweep: pf.Signal, minimum_frequency, maximum_frequency):
        self.sweep = sweep
        self.minimum_frequency = minimum_frequency
        self.maximum_frequency = maximum_frequency


class ISweep(ABC):
    @abstractmethod
    def generate(self) -> Sweep:
        pass


class WavSweep(ISweep):
    def __init__(self, sweep_file: str, minimum_frequency, maximum_frequency):
        self._sweep_file = sweep_file
        self._minimum_frequency = minimum_frequency
        self._maximum_frequency = maximum_frequency

    def generate(self) -> Sweep:
        return Sweep(pf.io.read_audio(self._sweep_file), self._minimum_frequency, self._maximum_frequency)


class ExponentialSweep(ISweep):
    def __init__(self, minimum_frequency, maximum_frequency, sampling_rate, duration, padding_time):
        self._minimum_frequency = minimum_frequency
        self._maximum_frequency = maximum_frequency
        self._sampling_rate = sampling_rate
        self._duration = duration
        self._padding_time = padding_time

    def generate(self) -> Sweep:
        x = pf.signals.exponential_sweep_time(
            n_samples=self._duration * self._sampling_rate,
            frequency_range=[self._minimum_frequency, self._maximum_frequency],
            sampling_rate=self._sampling_rate)
        x_padded = pf.dsp.pad_zeros(x, pad_width=math.floor(self._padding_time * x.sampling_rate))
        return Sweep(x_padded, self._minimum_frequency, self._maximum_frequency)


class SweepFactory:
    @staticmethod
    def create(config_file: str) -> ISweep:
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        section = 'sweep'

        type_to_build = config_parser.get(section, 'type')

        if type_to_build == 'WavSweep':
            return WavSweep(config_file.get(section, 'wavfile'), config_parser.getfloat(section, 'minimum_frequency'), config_parser.getfloat(section, 'maximum_frequency'))
        elif type_to_build == 'ExponentialSweep':
            return ExponentialSweep(config_parser.getfloat(section, 'minimum_frequency'), config_parser.getfloat(section, 'maximum_frequency'), config_parser.getint(section, 'sampling_rate'), config_parser.getfloat(section, 'duration'), config_parser.getfloat(section, 'padding_time'))
        else:
            raise Exception(f'Unknown sweep type: {type_to_build}')


class IAudio(ABC):
    """
    Interface for audio-related operations.

    This abstract base class provides a contract for classes that implement
    audio measurements, specifically for measuring impulse responses (IR).
    It ensures that any subclass implements necessary functionality for IR
    measurement using specific positional data.
    """
    @abstractmethod
    def measure_ir(self, position: CylindricalPosition) -> None:
        pass


class Audio(IAudio):
    """
    Handles audio-related operations including impulse response measurement
    using an exponential sweep signal.
    """
    def __init__(self,
                 device_id: Union[int, tuple[int, int]],
                 sweep: Sweep,
                 pre_sweeps: int,
                 measurement_sweeps: int,
                 save_raw_measurement: bool):
        # sounddevice accepts either an int or a (input, output) pair
        sd.default.device = device_id
        self._sweep = sweep
        self._pre_sweeps = pre_sweeps
        self._measurement_sweeps = measurement_sweeps
        self._save_raw_measurement = save_raw_measurement

    def measure_ir(self, position: CylindricalPosition) -> None:
        """
        Takes an impulse response using an exponential sweep.
        It stores the result in a file with the position encoded in its name.
        """
        logger.info(f'IR measurement for position {position}')
        sampling_rate = self._sweep.sweep.sampling_rate

        sweep_time = self._sweep.sweep.time.T
        sweep_length = sweep_time.shape[0]
        sweep_repetitions = self._pre_sweeps + self._measurement_sweeps

        pause_seconds = 0.0
        pause_samples = int(round(pause_seconds * sampling_rate))
        pause_stereo = np.zeros((pause_samples, 2), dtype=sweep_time.dtype)

        play_signal = np.concatenate((sweep_time, sweep_time), axis=1)
        play_with_pause = np.concatenate((play_signal, pause_stereo), axis=0)
        play_signal = np.tile(play_with_pause, (sweep_repetitions, 1))

        recording = sd.playrec(
            play_signal,
            sampling_rate,
            channels=2,
            blocking=True)

        filename = f'({position.r():.1f}, {position.t():.1f}, {position.z():.1f}).wav'

        if self._save_raw_measurement:
            recording_signal = pf.Signal(recording.T, sampling_rate)
            pf.io.write_audio(
                recording_signal,
                os.path.join('./RawRecordings', filename),
                'DOUBLE'
            )

        block_length = sweep_length + pause_samples
        blocks = recording.reshape(sweep_repetitions, block_length, 2)
        sweeps = blocks[:, :sweep_length, :]
        averaged = sweeps[self._pre_sweeps:, :, :].mean(axis=0)

        y = pf.Signal(averaged[:, 0].T, sampling_rate)
        x_reference = pf.Signal(averaged[:, 1].T, sampling_rate)

        x_inverted = pf.dsp.regularized_spectrum_inversion(x_reference,
                                                           (self._sweep.minimum_frequency, self._sweep.maximum_frequency))
        h = y * x_inverted

        # apply high-pass to reject out-of-band noise
        h_processed = pf.dsp.filter.butterworth(h, 8, 15, 'highpass')

        # window to reduce impulse response length
        h_processed = pf.dsp.time_window(h_processed, [0, 0.1], window='boxcar', unit='s', crop='window')

        pf.io.write_audio(
            h_processed,
            os.path.join('./Recordings', filename),
            'DOUBLE')


class AudioMock(IAudio):
    """
    Mock implementation of the IAudio interface.

    This class serves as a simulated audio system component for testing or
    development purposes. It provides a mock implementation of the methods
    defined in the IAudio interface without performing actual audio processing.
    """
    def __init__(self):
        pass

    def measure_ir(self, position: CylindricalPosition) -> None:
        logger.info(f'[MOCK] IR measurement for position {position}')


class AudioFactory:
    """
    Factory class responsible for creating instances of Audio or AudioMock
    based on configuration settings.
    """

    @staticmethod
    def _parse_device_id(raw: str) -> Union[int, tuple[int, int]]:
        """
        Accepts:
          - "7" -> 7
          - "12,7" (or "12, 7") -> (12, 7)  (input_id, output_id)
        """
        s = raw.strip()
        if "," not in s:
            return int(s)

        parts = [p.strip() for p in s.split(",") if p.strip() != ""]
        if len(parts) != 2:
            raise ValueError(
                f"device_id must be a single integer or two integers separated by a comma, got: {raw!r}"
            )
        return (int(parts[0]), int(parts[1]))

    @staticmethod
    def create(config_file: str, section: str) -> IAudio:
        """
        Factory method that builds an Audio object based on a config file
        :param section:
        :param config_file: config file name containing the config
        :return: an Audio object (or a mock)
        """
        config_parser = configparser.ConfigParser(inline_comment_prefixes="#")
        config_parser.read(config_file)

        mock = config_parser.getboolean(section, 'mock')
        if mock:
            return AudioMock()

        device_raw = config_parser.get(section, 'device_id')
        device_id = AudioFactory._parse_device_id(device_raw)

        sweep_generator = SweepFactory.create(config_file)
        sweep = sweep_generator.generate()
        pre_sweeps = config_parser.getint(section, 'pre_sweeps')
        measurement_sweeps = config_parser.getint(section, 'measurement_sweeps')
        save_raw_measurement = config_parser.getboolean(section, 'save_raw_measurement')
        return Audio(device_id, sweep, pre_sweeps, measurement_sweeps, save_raw_measurement)
