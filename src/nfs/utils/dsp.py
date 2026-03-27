import numpy as np
import math
from typing import Tuple


class DSPUtils:
    """Static utilities for pure mathematical operations."""

    @staticmethod
    def fmt_num_for_name(val) -> str:
        """Formats a number for safe filenames (e.g., 4.5 -> '4p5')."""
        if val is None:
            return "NA"
        return str(val).replace(".", "p")

    @staticmethod
    def db_to_lin(db: float) -> float:
        """Converts dBFS to linear amplitude scale."""
        return 10 ** (db / 20.0)

    @staticmethod
    def hann_fade(sig: np.ndarray, fade_ms: float, fs: int, side: str = "both") -> np.ndarray:
        """
        Applies a Hann window fade to the signal edges to prevent spectral leakage (clicks).
        
        Args:
            :param fs: sample rate
            :param fade_ms: fade duration in ms
            :param sig: signal to fade
            :param side: 'in' (start), 'out' (end), or 'both'.
        """
        n_fade = int(round(fade_ms / 1000.0 * fs))
        if n_fade <= 0 or n_fade >= len(sig):
            return sig

        # Generate ramp: 0 to 1 (half-cosine)
        ramp = 0.5 * (1 - np.cos(np.pi * np.arange(n_fade) / (n_fade - 1)))
        y = sig.copy()

        if side in ["both", "in"]:
            y[:n_fade] *= ramp

        if side in ["both", "out"]:
            # Fade out uses the reverse of the ramp
            y[-n_fade:] *= ramp[::-1]

        return y

    @staticmethod
    def rfft_xcorr(a: np.ndarray, b: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Fast Cross-Correlation via RFFT."""
        # Calculate next power of 2 for optimal FFT performance and linear convolution
        n = int(2 ** np.ceil(np.log2(len(a) + len(b) - 1)))

        # Transform signal 'a' and 'b' into the frequency domain
        A = np.fft.rfft(a, n=n)
        B = np.fft.rfft(b, n=n)

        # Multiply by complex conjugate to perform correlation, then return to time domain
        x = np.fft.irfft(A * np.conj(B), n=n)

        # Shift the zero-lag component to the center of the array
        x = np.roll(x, len(b) - 1)

        # Create an array of lag indices corresponding to the shifted signal
        lags = np.arange(-len(b) + 1, len(a))

        # Return the lag indices and the correlation result trimmed to the valid range
        return lags, x[:len(lags)]
