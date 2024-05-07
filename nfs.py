import pyfar as pf
import numpy as np
import sounddevice as sd
import matplotlib.pyplot as plt
import os
from scanner import Scanner, CylindricalPosition


class NearFieldScanner:
    def __init__(self, scanner):
        self._scanner = scanner

    def take_single_measurement(self):
        x = pf.signals.exponential_sweep_time(
            n_samples=2 ** 16,
            frequency_range=[20, 22050],
            sampling_rate=44100)

        # ax = pf.plot.time_freq(x)
        # ax[0].set_title('Exponential sweep excitation signal')

        x_padded = pf.dsp.pad_zeros(x, pad_width=1 * x.sampling_rate)

        recording = sd.playrec(
            x_padded.time.T, x_padded.sampling_rate, channels=2,
            blocking=True)

        y = pf.Signal(recording[:, 0].T, x_padded.sampling_rate)
        x_reference = pf.Signal(recording[:, 1].T, x_padded.sampling_rate)

        ax = pf.plot.time_freq(y)
        ax[0].set_title('Recorded signal response $y$')

        x_inverted = pf.dsp.regularized_spectrum_inversion(x_reference, (20, 21000))
        h = y * x_inverted

        # apply high-pass to reject out of band noise
        h_processed = pf.dsp.filter.butterworth(h, 8, 20, 'highpass')

        # window to reduce impulse response length
        h_processed = pf.dsp.time_window(
            h_processed, [0, 0.1], unit='s', crop='window')

        pf.io.write_audio(
            x_inverted,  # h_processed,
            os.path.join('Recordings', 'impulse_response_measurement.wav'),
            'DOUBLE')

    def take_measurement_set(self):
        self._scanner.move_to(CylindricalPosition(10, 0, 0))
        self.take_single_measurement()
