First attempt to get all things in place to have a python controlled Near Field Scanner.

Initial implementation was in Octave, but although it worked quite nice in a pra-alpha state, python seemed to be a more allround programming language.


Idea to test orthogonality of the scanner by using sound:

import numpy as np
import scipy.signal as signal
from scipy.io import wavfile

import numpy as np
import scipy.signal as signal
from scipy.io import wavfile

def get_gated_distance(ref_file, rec_file, expected_dist_m, window_width_m=0.2, temp_c=20):
    fs, ref_data = wavfile.read(ref_file)
    fs_rec, rec_data = wavfile.read(rec_file)
    
    # Calculate speed of sound
    c = 331.3 * np.sqrt(1 + temp_c / 273.15)
    
    # 1. Generate Impulse Response via Cross-Correlation
    # We use 'full' to ensure we don't clip the timing
    correlation = signal.correlate(rec_data, ref_data, mode='full')
    lags = signal.correlation_lags(len(rec_data), len(ref_data), mode='full')
    
    # 2. Define the Time Gate (in samples)
    # Search only near where we expect the mic to be
    expected_samples = (expected_dist_m / c) * fs
    window_samples = (window_width_m / c) * fs
    
    lower_bound = expected_samples - (window_samples / 2)
    upper_bound = expected_samples + (window_samples / 2)
    
    # 3. Find the peak ONLY within that gate
    mask = (lags >= lower_bound) & (lags <= upper_bound)
    gated_corr = np.abs(correlation[mask])
    gated_lags = lags[mask]
    
    if len(gated_corr) == 0:
        return None, "No signal found in window"

    local_peak_idx = np.argmax(gated_corr)
    peak_lag = gated_lags[local_peak_idx]
    
    # 4. Parabolic Interpolation for 1mm precision
    # We look at the samples immediately to the left and right of the peak
    idx = np.where(lags == peak_lag)[0][0]
    y1, y2, y3 = correlation[idx-1], correlation[idx], correlation[idx+1]
    
    # Quadratic peak formula
    offset = (y3 - y1) / (2 * (2 * y2 - y1 - y3))
    refined_lag = peak_lag + offset
    
    # 5. Result
    final_distance = (refined_lag / fs) * c
    return final_distance, None

# Usage for a mic you expect is at 0.5 meters:
# dist, err = get_gated_distance('ref.wav', 'rec.wav', expected_dist_m=0.5)

Fixed Latency: If you are using a standard Windows sound driver (MME/DirectX), the "latency" can change every time you hit record. Use ASIO drivers to ensure the time between "Start Record" and "Start Playback" is sample-accurate and identical every single time.
