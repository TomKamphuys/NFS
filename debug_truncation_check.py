"""Verify that the near-field rolloff is due to SH truncation, not Hankel issues."""
import numpy as np
from nfs.datatypes import CylindricalPosition
from nfs.utils.spatial import (
    SphericalHarmonicSpeakerModel,
    DiscretePistonSpeakerModel,
    SPEED_OF_SOUND,
)

fs = 48000
num_bins = fs // 2 + 1
freqs = np.linspace(0, fs / 2.0, num_bins)

# The key question: does the rolloff depend on SH order?
# If it's SH truncation, higher order should reduce it.
# If it's a fundamental Hankel issue, order won't help.

print("=== FR at 560mm on-axis, various SH orders ===")
print("(If rolloff decreases with order, it's SH truncation)\n")

discrete_model = DiscretePistonSpeakerModel(piston_point_count=100)
H_disc = discrete_model.compute_frequency_response(fs, CylindricalPosition(560, 0, 0), num_bins=num_bins)

for order in [2, 4, 8, 16]:
    model = SphericalHarmonicSpeakerModel(order=order)
    H = model.compute_frequency_response(fs, CylindricalPosition(560, 0, 0), num_bins=num_bins)
    mag_db = 20 * np.log10(np.abs(H) + 1e-12)
    
    idx_1k = int(1000 / (fs / 2) * (num_bins - 1))
    idx_4k = int(4000 / (fs / 2) * (num_bins - 1))
    idx_8k = int(8000 / (fs / 2) * (num_bins - 1))
    
    disc_1k = 20 * np.log10(np.abs(H_disc[idx_1k]) + 1e-12)
    disc_4k = 20 * np.log10(np.abs(H_disc[idx_4k]) + 1e-12)
    disc_8k = 20 * np.log10(np.abs(H_disc[idx_8k]) + 1e-12)
    
    print(f"Order {order:>2d}: 1kHz={mag_db[idx_1k]:6.1f}dB, 4kHz={mag_db[idx_4k]:6.1f}dB, "
          f"8kHz={mag_db[idx_8k]:6.1f}dB")

print(f"Discrete: 1kHz={disc_1k:6.1f}dB, 4kHz={disc_4k:6.1f}dB, 8kHz={disc_8k:6.1f}dB")

# Now check: the user's actual scenario
# They say 680mm is ok, 560mm shows attenuation
# Let's check the rolloff difference between distances

print("\n=== FR rolloff (8kHz - 1kHz) at various distances ===")
print("(More negative = more high-frequency attenuation)\n")

for order in [4, 8, 16]:
    model = SphericalHarmonicSpeakerModel(order=order)
    print(f"Order {order}:")
    for d in [400, 500, 560, 600, 680, 800, 1000]:
        H = model.compute_frequency_response(fs, CylindricalPosition(d, 0, 0), num_bins=num_bins)
        mag_db = 20 * np.log10(np.abs(H) + 1e-12)
        idx_1k = int(1000 / (fs / 2) * (num_bins - 1))
        idx_8k = int(8000 / (fs / 2) * (num_bins - 1))
        rolloff = mag_db[idx_8k] - mag_db[idx_1k]
        print(f"  r={d:>4d}mm: rolloff={rolloff:6.1f}dB (8kHz vs 1kHz)")
    print()

# Check kr values to understand where truncation matters
print("=== kr analysis ===")
print("SH truncation error is significant when kr > order")
print("(The expansion needs order >= kr for accurate representation)\n")

for d in [400, 560, 680, 800, 1000]:
    r = d / 1000.0
    k_8k = 2 * np.pi * 8000 / SPEED_OF_SOUND
    kr_8k = k_8k * r
    print(f"  r={d}mm: kr at 8kHz = {kr_8k:.1f} (need order >= {int(kr_8k)})")
