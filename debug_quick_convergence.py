"""Quick convergence test - fewer orders, no plotting."""
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

# The problematic position
pos = CylindricalPosition(560, 0, 450)
pos_on_axis = CylindricalPosition(400, 0, 0)

discrete_model = DiscretePistonSpeakerModel(piston_point_count=100)

print("=== Off-axis (r=560mm, z=450mm) convergence ===")
for order in [4, 8, 12, 16]:
    model = SphericalHarmonicSpeakerModel(order=order)
    H = model.compute_frequency_response(fs, pos, num_bins=num_bins)
    idx_1k = int(1000 / (fs / 2) * (num_bins - 1))
    idx_4k = int(4000 / (fs / 2) * (num_bins - 1))
    idx_8k = int(8000 / (fs / 2) * (num_bins - 1))
    db_1k = 20 * np.log10(np.abs(H[idx_1k]) + 1e-12)
    db_4k = 20 * np.log10(np.abs(H[idx_4k]) + 1e-12)
    db_8k = 20 * np.log10(np.abs(H[idx_8k]) + 1e-12)
    print(f"  Order {order}: 1kHz={db_1k:6.1f}dB, 4kHz={db_4k:6.1f}dB, 8kHz={db_8k:6.1f}dB")

H_disc = discrete_model.compute_frequency_response(fs, pos, num_bins=num_bins)
idx_1k = int(1000 / (fs / 2) * (num_bins - 1))
idx_4k = int(4000 / (fs / 2) * (num_bins - 1))
idx_8k = int(8000 / (fs / 2) * (num_bins - 1))
print(f"  Discrete: 1kHz={20*np.log10(np.abs(H_disc[idx_1k])+1e-12):6.1f}dB, "
      f"4kHz={20*np.log10(np.abs(H_disc[idx_4k])+1e-12):6.1f}dB, "
      f"8kHz={20*np.log10(np.abs(H_disc[idx_8k])+1e-12):6.1f}dB")

print("\n=== On-axis at 400mm convergence ===")
for order in [4, 8, 12, 16]:
    model = SphericalHarmonicSpeakerModel(order=order)
    H = model.compute_frequency_response(fs, pos_on_axis, num_bins=num_bins)
    idx_1k = int(1000 / (fs / 2) * (num_bins - 1))
    idx_4k = int(4000 / (fs / 2) * (num_bins - 1))
    idx_8k = int(8000 / (fs / 2) * (num_bins - 1))
    db_1k = 20 * np.log10(np.abs(H[idx_1k]) + 1e-12)
    db_4k = 20 * np.log10(np.abs(H[idx_4k]) + 1e-12)
    db_8k = 20 * np.log10(np.abs(H[idx_8k]) + 1e-12)
    print(f"  Order {order}: 1kHz={db_1k:6.1f}dB, 4kHz={db_4k:6.1f}dB, 8kHz={db_8k:6.1f}dB")

H_disc = discrete_model.compute_frequency_response(fs, pos_on_axis, num_bins=num_bins)
print(f"  Discrete: 1kHz={20*np.log10(np.abs(H_disc[idx_1k])+1e-12):6.1f}dB, "
      f"4kHz={20*np.log10(np.abs(H_disc[idx_4k])+1e-12):6.1f}dB, "
      f"8kHz={20*np.log10(np.abs(H_disc[idx_8k])+1e-12):6.1f}dB")

# Now test: what does the user's actual scenario look like?
# They say 680mm is ok, 560mm shows attenuation
# Let's test on-axis at various distances with order=8
print("\n=== On-axis at various distances (order=8) ===")
model8 = SphericalHarmonicSpeakerModel(order=8)
for d in [400, 500, 560, 600, 680, 800, 1000]:
    pos_d = CylindricalPosition(d, 0, 0)
    H = model8.compute_frequency_response(fs, pos_d, num_bins=num_bins)
    idx_4k = int(4000 / (fs / 2) * (num_bins - 1))
    db_4k = 20 * np.log10(np.abs(H[idx_4k]) + 1e-12)
    print(f"  r={d}mm: 4kHz={db_4k:6.1f}dB")

# The user's measurement grid likely has z-offsets. Let's simulate a spherical grid
# where the measurement distance is fixed but the position varies in z
print("\n=== Spherical grid simulation: fixed distance, varying z offset ===")
print("(This simulates what happens in a real spherical measurement)")
for dist_mm in [560, 680, 800]:
    print(f"\n  Fixed distance {dist_mm}mm:")
    for z_mm in [0, 100, 200, 300, 400]:
        # r_cylindrical = sqrt(dist^2 - z^2)
        r_cyl = np.sqrt(dist_mm**2 - z_mm**2)
        pos = CylindricalPosition(r_cyl, 0, z_mm)
        H = model8.compute_frequency_response(fs, pos, num_bins=num_bins)
        idx_4k = int(4000 / (fs / 2) * (num_bins - 1))
        db_4k = 20 * np.log10(np.abs(H[idx_4k]) + 1e-12)
        actual_r = np.sqrt(r_cyl**2 + z_mm**2)
        print(f"    z={z_mm:>3d}mm (r_cyl={r_cyl:.0f}mm, actual_r={actual_r:.0f}mm): 4kHz={db_4k:6.1f}dB")
