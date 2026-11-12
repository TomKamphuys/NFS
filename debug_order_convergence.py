"""Test if increasing SH order fixes the near-field attenuation issue."""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from nfs.datatypes import CylindricalPosition
from nfs.utils.spatial import (
    SphericalHarmonicSpeakerModel,
    DiscretePistonSpeakerModel,
    SPEED_OF_SOUND,
)

fs = 48000
num_bins = fs // 2 + 1
freqs = np.linspace(0, fs / 2.0, num_bins)

# Test the problematic position: 560mm radial, 450mm z offset
# This gives actual distance ~718mm at theta ~39deg
pos = CylindricalPosition(560, 0, 450)

# Also test a closer on-axis position
pos_on_axis = CylindricalPosition(400, 0, 0)

# Test with different orders
orders = [4, 8, 12, 16, 20, 24]

fig, axes = plt.subplots(2, 2, figsize=(14, 10))

# Plot 1: Off-axis position (560mm r, 450mm z) at various orders
ax1 = axes[0, 0]
for order in orders:
    model = SphericalHarmonicSpeakerModel(order=order)
    H = model.compute_frequency_response(fs, pos, num_bins=num_bins)
    mag_db = 20 * np.log10(np.abs(H) + 1e-12)
    mask = freqs <= 8000
    ax1.plot(freqs[mask] / 1000, mag_db[mask], label=f'order={order}')

# Add discrete piston for reference
discrete_model = DiscretePistonSpeakerModel(piston_point_count=100)
H_disc = discrete_model.compute_frequency_response(fs, pos, num_bins=num_bins)
mag_disc = 20 * np.log10(np.abs(H_disc) + 1e-12)
mask = freqs <= 8000
ax1.plot(freqs[mask] / 1000, mag_disc[mask], 'k--', label='Discrete piston', linewidth=2)

ax1.set_xlabel('Frequency (kHz)')
ax1.set_ylabel('Magnitude (dB)')
ax1.set_title('Off-axis (r=560mm, z=450mm): SH order convergence')
ax1.legend(fontsize=8)
ax1.grid(True, alpha=0.3)
ax1.set_ylim(-40, 10)

# Plot 2: On-axis at 400mm, various orders
ax2 = axes[0, 1]
for order in orders:
    model = SphericalHarmonicSpeakerModel(order=order)
    H = model.compute_frequency_response(fs, pos_on_axis, num_bins=num_bins)
    mag_db = 20 * np.log10(np.abs(H) + 1e-12)
    mask = freqs <= 8000
    ax2.plot(freqs[mask] / 1000, mag_db[mask], label=f'order={order}')

H_disc = discrete_model.compute_frequency_response(fs, pos_on_axis, num_bins=num_bins)
mag_disc = 20 * np.log10(np.abs(H_disc) + 1e-12)
ax2.plot(freqs[mask] / 1000, mag_disc[mask], 'k--', label='Discrete piston', linewidth=2)

ax2.set_xlabel('Frequency (kHz)')
ax2.set_ylabel('Magnitude (dB)')
ax2.set_title('On-axis at 400mm: SH order convergence')
ax2.legend(fontsize=8)
ax2.grid(True, alpha=0.3)
ax2.set_ylim(-40, 10)

# Plot 3: Off-axis at various distances, order=8 vs order=20
ax3 = axes[1, 0]
distances = [300, 400, 500, 560, 680, 800, 1000]
model8 = SphericalHarmonicSpeakerModel(order=8)
model20 = SphericalHarmonicSpeakerModel(order=20)

for d in distances:
    pos_z = CylindricalPosition(d, 0, d * 0.8)  # ~39deg off-axis at various distances
    H8 = model8.compute_frequency_response(fs, pos_z, num_bins=num_bins)
    mag8 = 20 * np.log10(np.abs(H8) + 1e-12)
    mask = freqs <= 8000
    ax3.plot(freqs[mask] / 1000, mag8[mask], alpha=0.5, label=f'r={d}mm' if d == distances[0] else '')

# Order 20 for comparison at 560mm
pos_560 = CylindricalPosition(560, 0, 450)
H20 = model20.compute_frequency_response(fs, pos_560, num_bins=num_bins)
mag20 = 20 * np.log10(np.abs(H20) + 1e-12)
ax3.plot(freqs[mask] / 1000, mag20[mask], 'g-', linewidth=2.5, label='order=20, r=560mm')

ax3.set_xlabel('Frequency (kHz)')
ax3.set_ylabel('Magnitude (dB)')
ax3.set_title('Off-axis at various distances (order=8) vs order=20 at 560mm')
ax3.legend(fontsize=7)
ax3.grid(True, alpha=0.3)
ax3.set_ylim(-40, 10)

# Plot 4: Convergence table - magnitude at 4kHz for off-axis position
ax4 = axes[1, 1]
ax4.axis('off')

convergence_data = []
for order in [4, 8, 12, 16, 20, 24, 28, 32]:
    model = SphericalHarmonicSpeakerModel(order=order)
    H = model.compute_frequency_response(fs, pos, num_bins=num_bins)
    idx_4k = int(4000 / (fs / 2) * (num_bins - 1))
    db_4k = 20 * np.log10(np.abs(H[idx_4k]) + 1e-12)
    idx_8k = int(8000 / (fs / 2) * (num_bins - 1))
    db_8k = 20 * np.log10(np.abs(H[idx_8k]) + 1e-12)
    convergence_data.append(f"Order {order:>2d}: 4kHz={db_4k:6.1f}dB, 8kHz={db_8k:6.1f}dB")

H_disc = discrete_model.compute_frequency_response(fs, pos, num_bins=num_bins)
idx_4k = int(4000 / (fs / 2) * (num_bins - 1))
disc_4k = 20 * np.log10(np.abs(H_disc[idx_4k]) + 1e-12)
idx_8k = int(8000 / (fs / 2) * (num_bins - 1))
disc_8k = 20 * np.log10(np.abs(H_disc[idx_8k]) + 1e-12)
convergence_data.append(f"Discrete:  4kHz={disc_4k:6.1f}dB, 8kHz={disc_8k:6.1f}dB")

table_text = "Convergence at off-axis position (r=560mm, z=450mm):\n\n" + "\n".join(convergence_data)
ax4.text(0.1, 0.5, table_text, fontsize=10, family='monospace', verticalalignment='center',
         boundingbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

plt.tight_layout()
plt.savefig('debug_order_convergence.png', dpi=150)
print("Saved: debug_order_convergence.png")

# Print the convergence data
print("\n=== Convergence at off-axis position (r=560mm, z=450mm) ===")
for line in convergence_data:
    print(f"  {line}")

# Check what kr_max is needed for the near field
print("\n=== kr analysis ===")
r_actual = 0.718  # actual distance for the off-axis position
for f in [1000, 2000, 4000, 8000, 12000, 20000]:
    k = 2 * np.pi * f / SPEED_OF_SOUND
    kr = k * r_actual
    print(f"  f={f:>5d}Hz: kr={kr:.1f} (need order >= {int(kr)} for convergence)")
