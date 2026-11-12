"""Diagnostic script to investigate near-field high-frequency attenuation in the SH speaker model."""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from nfs.datatypes import CylindricalPosition
from nfs.utils.spatial import (
    SphericalHarmonicSpeakerModel,
    DiscretePistonSpeakerModel,
    spherical_bessel_hankel,
    SPEED_OF_SOUND,
)

fs = 48000
num_bins = fs // 2 + 1
freqs = np.linspace(0, fs / 2.0, num_bins)

# Test distances in mm (r cylindrical coordinate)
distances_mm = [400, 500, 560, 600, 680, 800, 1000]

# Create models
sh_model = SphericalHarmonicSpeakerModel(order=8)
discrete_model = DiscretePistonSpeakerModel(piston_point_count=100)

fig, axes = plt.subplots(2, 2, figsize=(14, 10))

# --- Plot 1: SH model frequency response at various distances ---
ax1 = axes[0, 0]
for d in distances_mm:
    pos = CylindricalPosition(d, 0.0, 0.0)  # On-axis, front of speaker
    H = sh_model.compute_frequency_response(fs, pos, num_bins=num_bins)
    mag_db = 20 * np.log10(np.abs(H) + 1e-12)
    # Only plot up to 8kHz for clarity
    mask = freqs <= 8000
    ax1.plot(freqs[mask] / 1000, mag_db[mask], label=f'{d}mm')
ax1.set_xlabel('Frequency (kHz)')
ax1.set_ylabel('Magnitude (dB)')
ax1.set_title('SH Model: On-axis FR at various distances')
ax1.legend(fontsize=8)
ax1.grid(True, alpha=0.3)
ax1.set_ylim(-60, 10)

# --- Plot 2: Discrete piston model at various distances ---
ax2 = axes[0, 1]
for d in distances_mm:
    pos = CylindricalPosition(d, 0.0, 0.0)
    H = discrete_model.compute_frequency_response(fs, pos, num_bins=num_bins)
    mag_db = 20 * np.log10(np.abs(H) + 1e-12)
    mask = freqs <= 8000
    ax2.plot(freqs[mask] / 1000, mag_db[mask], label=f'{d}mm')
ax2.set_xlabel('Frequency (kHz)')
ax2.set_ylabel('Magnitude (dB)')
ax2.set_title('Discrete Piston Model: On-axis FR at various distances')
ax2.legend(fontsize=8)
ax2.grid(True, alpha=0.3)
ax2.set_ylim(-60, 10)

# --- Plot 3: Radial Hankel ratio for l=0 at 560mm ---
ax3 = axes[1, 0]
r_m = 0.560  # 560mm
r_ref = 1.0
k = 2.0 * np.pi * freqs / SPEED_OF_SOUND
kr = k * r_m
kr_ref = k * r_ref

for l in [0, 1, 2, 4, 8]:
    h_r = spherical_bessel_hankel(l, kr)
    h_ref = spherical_bessel_hankel(l, kr_ref)
    ratio = np.where(np.abs(h_ref) > 1e-12, h_r / h_ref, 1.0)
    ratio_db = 20 * np.log10(np.abs(ratio) + 1e-12)
    mask = freqs <= 8000
    ax3.plot(freqs[mask] / 1000, ratio_db[mask], label=f'l={l}')
ax3.set_xlabel('Frequency (kHz)')
ax3.set_ylabel('Radial Ratio (dB)')
ax3.set_title(f'Hankel radial ratio hₗ(kr)/hₗ(kr₀) at r={r_m*1000:.0f}mm')
ax3.legend(fontsize=8)
ax3.grid(True, alpha=0.3)
ax3.set_ylim(-60, 10)

# --- Plot 4: kr values at different distances and frequencies ---
ax4 = axes[1, 1]
for d in distances_mm:
    r = d / 1000.0
    k = 2.0 * np.pi * freqs / SPEED_OF_SOUND
    kr_vals = k * r
    mask = freqs <= 8000
    ax4.semilogy(freqs[mask] / 1000, kr_vals[mask], label=f'r={d}mm')
ax4.axhline(y=0, color='k', linestyle='--', alpha=0.3)
# Mark where kr = l for various l values
for l in [0, 1, 2, 4, 8]:
    ax4.axhline(y=l, color='gray', linestyle=':', alpha=0.5, label=f'l={l}' if l > 0 else '')
ax4.set_xlabel('Frequency (kHz)')
ax4.set_ylabel('kr = k × r')
ax4.set_title('Wavenumber-distance product (kr) at various distances')
ax4.legend(fontsize=8)
ax4.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('debug_nearfield_fr.png', dpi=150)
print("Saved: debug_nearfield_fr.png")

# --- Print some key values ---
print("\n=== Key diagnostics at 560mm, on-axis ===")
r_m = 0.560
pos = CylindricalPosition(560, 0.0, 0.0)
H_sh = sh_model.compute_frequency_response(fs, pos, num_bins=num_bins)
H_disc = discrete_model.compute_frequency_response(fs, pos, num_bins=num_bins)

for freq_hz in [100, 500, 1000, 2000, 4000, 8000]:
    idx = int(freq_hz / (fs / 2) * (num_bins - 1))
    k_val = 2 * np.pi * freq_hz / SPEED_OF_SOUND
    kr_val = k_val * r_m
    sh_db = 20 * np.log10(np.abs(H_sh[idx]) + 1e-12)
    disc_db = 20 * np.log10(np.abs(H_disc[idx]) + 1e-12)
    print(f"f={freq_hz:>6d}Hz: kr={kr_val:.2f}, SH={sh_db:7.1f}dB, Discrete={disc_db:7.1f}dB")

# Check the angular sum contributions per l
print("\n=== Per-l contribution at 560mm, 4kHz ===")
from nfs.utils.spatial import real_spherical_harmonics, cylindrical_to_spherical

r_m, theta, phi = cylindrical_to_spherical(pos, None)
k_4k = 2 * np.pi * 4000 / SPEED_OF_SOUND
kr_4k = k_4k * r_m
kr_ref_4k = k_4k * r_ref

sh_vals = real_spherical_harmonics(8, theta, phi)
idx_4k = int(4000 / (fs / 2) * (num_bins - 1))

for l in range(9):
    h_r = spherical_bessel_hankel(l, np.array([kr_4k]))
    h_ref = spherical_bessel_hankel(l, np.array([kr_ref_4k]))
    ratio = h_r[0] / h_ref[0] if np.abs(h_ref[0]) > 1e-12 else 1.0
    
    angular_sum = 0.0
    for m in range(-l, l + 1):
        key = (l, m)
        if key not in sh_model._coeffs:
            continue
        a_val = sh_model._interpolate_coeffs(sh_model._coeffs[key], sh_model._freq_bins, num_bins)[idx_4k]
        y_val = sh_vals.get(key, 0.0)
        if y_val != 0.0:
            angular_sum += a_val * y_val
    
    contribution = angular_sum * ratio
    print(f"l={l}: kr={kr_4k:.2f}, |ratio|={np.abs(ratio):.4f}, |angular_sum|={np.abs(angular_sum):.4f}, |contribution|={np.abs(contribution):.4f}")
