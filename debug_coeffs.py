"""Quick check of SH coefficient signs."""
import sys
import os
import math
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from nfs.utils.spatial import SphericalHarmonicSpeakerModel, real_spherical_harmonics

model = SphericalHarmonicSpeakerModel(order=4)
fs = 48000
num_bins = model._freq_bins

print("SH coefficient signs at 5000 Hz")
print("=" * 60)

freq = 5000
bin_idx = int(freq / (fs / 2) * (num_bins - 1))

# Print all non-zero coefficients
print(f"\nNon-zero coefficients at {freq} Hz (bin {bin_idx}):")
for (l, m), a_lm in sorted(model._coeffs.items()):
    val = a_lm[bin_idx]
    if abs(val) > 1e-6:
        print(f"  a_{l},{m} = {val:+.6f}")

# Check reconstruction at key points
print("\nReconstruction at key points (theta=90°, varying phi):")
print("-" * 60)

sh_front = real_spherical_harmonics(4, math.pi / 2.0, 0.0)
sh_back = real_spherical_harmonics(4, math.pi / 2.0, math.pi)
sh_right = real_spherical_harmonics(4, math.pi / 2.0, math.pi / 2.0)

D_front = 0.0
D_back = 0.0
D_right = 0.0

for (l, m), a_lm in model._coeffs.items():
    val = a_lm[bin_idx]
    D_front += val * sh_front[(l, m)]
    D_back += val * sh_back[(l, m)]
    D_right += val * sh_right[(l, m)]

print(f"  Front (phi=0):   D = {D_front:.6f}, |D| = {abs(D_front):.6f}")
print(f"  Back  (phi=180): D = {D_back:.6f}, |D| = {abs(D_back):.6f}")
print(f"  Right(phi=90):   D = {D_right:.6f}, |D| = {abs(D_right):.6f}")

# Check individual term contributions
print("\nTerm-by-term contribution at front (phi=0):")
print("-" * 60)
for (l, m), a_lm in sorted(model._coeffs.items()):
    val = a_lm[bin_idx]
    if abs(val) > 1e-6:
        y = sh_front[(l, m)]
        contrib = val * y
        print(f"  a_{l},{m} * Y_{l},{m} = {val:+.6f} * {y:+.6f} = {contrib:+.6f}")

print("\nTerm-by-term contribution at back (phi=180):")
print("-" * 60)
for (l, m), a_lm in sorted(model._coeffs.items()):
    val = a_lm[bin_idx]
    if abs(val) > 1e-6:
        y = sh_back[(l, m)]
        contrib = val * y
        print(f"  a_{l},{m} * Y_{l},{m} = {val:+.6f} * {y:+.6f} = {contrib:+.6f}")

# Check the raw directivity values used in projection
print("\nRaw directivity D at quadrature points (theta=90°, key phi values):")
print("-" * 60)
from scipy.special import j1

SPEED_OF_SOUND = 343.0
piston_radius_m = 0.1016
ka = 2 * math.pi * freq * piston_radius_m / SPEED_OF_SOUND

for phi_deg in [0, 90, 180, 270]:
    phi_rad = math.radians(phi_deg)
    cos_alpha = math.sin(math.pi / 2.0) * math.cos(phi_rad)  # sin(90)=1
    sin_alpha = math.sqrt(1.0 - cos_alpha ** 2)
    x = ka * sin_alpha
    if x > 1e-12:
        D_baffled = 2.0 * j1(x) / x
    else:
        D_baffled = 1.0
    D_free = D_baffled * (1.0 + cos_alpha) / 2.0
    print(f"  phi={phi_deg:3d}°: cos_alpha={cos_alpha:+.4f}, sin_alpha={sin_alpha:.4f}, "
          f"D_baffled={D_baffled:.4f}, D_free={D_free:.4f}")

# What is the actual max of the SH sum?
print("\nFinding the actual max of |D| over phi at theta=90°:")
print("-" * 60)
max_mag = 0
max_phi = 0
for phi_deg in range(0, 360, 5):
    phi_rad = math.radians(phi_deg)
    sh = real_spherical_harmonics(4, math.pi / 2.0, phi_rad)
    D = 0.0
    for (l, m), a_lm in model._coeffs.items():
        D += a_lm[bin_idx] * sh[(l, m)]
    mag = abs(D)
    if mag > max_mag:
        max_mag = mag
        max_phi = phi_deg

print(f"  Max |D| = {max_mag:.6f} at phi = {max_phi}°")

# Normalize and check
norm = abs(D_front)
print(f"\n  Normalization: |D_front| = {norm:.6f}")
print(f"  |D_back| / |D_front| = {abs(D_back) / (norm + 1e-12):.6f}")
print(f"  |D_right| / |D_front| = {abs(D_right) / (norm + 1e-12):.6f}")
