"""Quick test: does the discrete piston model produce different FR for different angles?"""
import sys
sys.path.insert(0, r"C:\Users\User\PycharmProjects\NFS\src")
import numpy as np
from nfs.datatypes import CylindricalPosition
from nfs.utils.spatial import DiscretePistonSpeakerModel

model = DiscretePistonSpeakerModel(piston_radius_m=0.1016, piston_point_count=100)
fs = 48000

# Test positions: same radius and z, varying t (azimuth angle)
test_positions = [
    CylindricalPosition(400.0, 0.0, 0.0),    # t=0: directly in front
    CylindricalPosition(400.0, 45.0, 0.0),   # t=45: side
    CylindricalPosition(400.0, 90.0, 0.0),   # t=90: 90 degrees
    CylindricalPosition(400.0, 180.0, 0.0),  # t=180: directly behind
    CylindricalPosition(400.0, 270.0, 0.0),  # t=270: 270 degrees
]

print("Testing discrete piston model variation with azimuth angle (t):")
print(f"{'t (deg)':>10} {'FR @ 1kHz (dB)':>16} {'FR @ 5kHz (dB)':>16} {'Peak amp':>12}")
print("-" * 60)

for pos in test_positions:
    H = model.compute_frequency_response(fs, pos)
    freqs = np.linspace(0, fs / 2, len(H))
    
    idx_1k = np.argmin(np.abs(freqs - 1000))
    idx_5k = np.argmin(np.abs(freqs - 5000))
    
    db_1k = 20 * np.log10(np.abs(H[idx_1k]) + 1e-12)
    db_5k = 20 * np.log10(np.abs(H[idx_5k]) + 1e-12)
    
    # Also test apply_to_signal
    signal = np.zeros(48000)
    signal[1000:2000] = np.sin(2 * np.pi * 1000 * np.arange(1000) / fs)
    filtered = model.apply_to_signal(signal, fs, pos)
    peak = np.max(np.abs(filtered))
    
    print(f"{pos.t():10.1f} {db_1k:16.2f} {db_5k:16.2f} {peak:12.6f}")

print("\nDirectivity check: unbaffled piston front and rear should match")
H_front = model.compute_frequency_response(fs, CylindricalPosition(400.0, 0.0, 0.0))
H_back = model.compute_frequency_response(fs, CylindricalPosition(400.0, 180.0, 0.0))
freqs = np.linspace(0, fs / 2, len(H_front))
idx = np.argmin(np.abs(freqs - 5000))
ratio_db = 20 * np.log10(np.abs(H_front[idx]) / (np.abs(H_back[idx]) + 1e-12))
print(f"At 5kHz: front-to-back difference is {ratio_db:.1f} dB")
assert abs(ratio_db) < 1e-9, "Unbaffled piston front and rear should match at 5kHz"
print("OK")
