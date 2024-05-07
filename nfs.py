import pytta
from scanner import Scanner, CylindricalPosition


class NearFieldScanner:
    def __init__(self, scanner):
        self._scanner = scanner

    def take_single_measurement(self):
        fs = 44100
        sweep = pytta.generate.sweep(freqMin=20, freqMax=20000, samplingRate=fs,
                                     fftDegree=18, startMargin=0.15, stopMargin=0.8,
                                     method='logarithmic', windowing='hann')
        ms = pytta.generate.measurement(kind='frf', excitation=sweep, samplingRate=fs,
                                        freqMin=20, freqMax=20000, device=[0, 1],
                                        inChannel=[1, 2], outChannel=[2, 3],
                                        comment='Example FRF measurement')
        m1 = ms.run()
        m1.plot_freq(smooth=True)

    def take_measurement_set(self):
        self._scanner.move_to(CylindricalPosition(10, 0, 0))
        self.take_single_measurement()
