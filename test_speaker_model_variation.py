"""Quick test: does the speaker model produce different FR for different angles?"""
import sys
sys.path.insert(0, r"C:\Users\User\PycharmProjects\NFS\src")
import numpy as np
from nfs.datatypes import CylindricalPosition
from nfs.utils.spatial import SphericalHarmonicSpeakerModel, cylindrical_to_spherical

model = SphericalHarmonicSpeakerModel(order=4)
fs = 48000

# Test positions: same radius and z, varying t (azimuth angle)
test_positions = [
    CylindricalPosition(400.0, 0.0, 0.0),    # t=0: directly in front
    CylindricalPosition(400.0, 45.0, 0.0),   # t=45: side
    CylindricalPosition(400.0, 90.0, 0.0),   # t=90: 90 degrees
    CylindricalPosition(400.0, 180.0, 0.0),  # t=180: directly behind
    CylindricalPosition(400.0, 270.0, 0.0),  # t=270: 270 degrees
]

print("Testing speaker model variation with azimuth angle (t):")
print(f"{'t (deg)':>10} {'theta (rad)':>12} {'phi (rad)':>12} {'FR @ 1kHz (dB)':>16} {'FR @ 5kHz (dB)':>16}")
print("-" * 70)

for pos in test_positions:
    r_m, theta, phi = cylindrical_to_spherical(pos)
    H = model.compute_frequency_response(fs, pos)
    freqs = np.linspace(0, fs / 2, len(H))
    
    # Find 1kHz and 5kHz bins
    idx_1k = np.argmin(np.abs(freqs - 1000))
    idx_5k = np.argmin(np.abs(freqs - 5000))
    
    db_1k = 20 * np.log10(np.abs(H[idx_1k]) + 1e-12)
    db_5k = 20 * np.log10(np.abs(H[idx_5k]) + 1e-12)
    
    print(f"{pos.t():10.1f} {theta:12.4f} {phi:12.4f} {db_1k:16.2f} {db_5k:16.2f}")

# Now test with signal filtering
print("\n\nTesting apply_to_signal with different positions:")
signal = np.zeros(48000)
signal[1000:2000] = np.sin(2 * np.pi * 1000 * np.arange(1000) / fs)  # 1kHz tone

for pos in test_positions:
    filtered = model.apply_to_signal(signal, fs, pos)
    peak = np.max(np.abs(filtered))
    print(f"t={pos.t():6.1f}°: peak amplitude = {peak:.6f}")
