"""Deep dive into the Hankel function behavior in the near-field regime."""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.special import spherical_jn, spherical_yn
from nfs.datatypes import CylindricalPosition
from nfs.utils.spatial import (
    SphericalHarmonicSpeakerModel,
    spherical_bessel_hankel,
    SPEED_OF_SOUND,
    cylindrical_to_spherical,
    real_spherical_harmonics,
)

fs = 48000
num_bins = fs // 2 + 1
freqs = np.linspace(0, fs / 2.0, num_bins)

print("=== Investigating Hankel function behavior ===\n")

# The key question: what happens to h_l(kr) when kr < l?
# For spherical Bessel/Hankel functions, when kr << l, the functions
# become evanescent (exponentially decaying). This is the "whispering gallery" regime.

# Let's check the magnitude of h_l(kr) for various l and kr values
print("Hankel magnitude |h_l(kr)| for various l at kr = l (transition point):")
for l in [0, 1, 2, 4, 6, 8, 10, 12]:
    kr = np.array([float(l) + 0.01])  # Just above l
    h = spherical_bessel_hankel(l, kr)
    j = spherical_jn(l, kr)
    y = spherical_yn(l, kr)
    print(f"  l={l:2d}, kr={kr[0]:.2f}: |h|={np.abs(h[0]):.4e}, |j|={np.abs(j[0]):.4e}, |y|={np.abs(y[0]):.4e}, phase={np.angle(h[0]):.3f}")

print("\nHankel magnitude |h_l(kr)| for various l at kr = l/2 (below transition):")
for l in [2, 4, 6, 8, 10, 12]:
    kr = np.array([float(l) / 2])
    h = spherical_bessel_hankel(l, kr)
    j = spherical_jn(l, kr)
    y = spherical_yn(l, kr)
    print(f"  l={l:2d}, kr={kr[0]:.2f}: |h|={np.abs(h[0]):.4e}, |j|={np.abs(j[0]):.4e}, |y|={np.abs(y[0]):.4e}, phase={np.angle(h[0]):.3f}")

# Now check what kr values correspond to at 560mm for various frequencies
print("\n=== kr values at 560mm for key frequencies ===")
r_m = 0.560
for f in [100, 500, 1000, 2000, 4000, 8000, 12000]:
    k = 2 * np.pi * f / SPEED_OF_SOUND
    kr = k * r_m
    print(f"  f={f:>5d}Hz: kr={kr:.2f}")

# The critical insight: for SH order 8, at 560mm, kr=8 at about f=2kHz
# Below 2kHz, kr < 8 for l=8, meaning the l=8 term is in the evanescent regime
# But the ratio h_l(kr)/h_l(kr_ref) should still be well-behaved...

# Let's check the ratio more carefully
print("\n=== Radial ratio h_l(kr)/h_l(kr_ref) at 560mm ===")
r_ref = 1.0
r_m = 0.560
for f in [100, 500, 1000, 2000, 4000]:
    k = 2 * np.pi * f / SPEED_OF_SOUND
    kr = k * r_m
    kr_ref = k * r_ref
    print(f"\n  f={f}Hz: kr={kr:.2f}, kr_ref={kr_ref:.2f}")
    for l in [0, 1, 2, 4, 6, 8]:
        h_r = spherical_bessel_hankel(l, np.array([kr]))
        h_ref = spherical_bessel_hankel(l, np.array([kr_ref]))
        ratio = h_r[0] / h_ref[0] if np.abs(h_ref[0]) > 1e-12 else 1.0
        print(f"    l={l}: |h(r)|={np.abs(h_r[0]):.4e}, |h(ref)|={np.abs(h_ref[0]):.4e}, |ratio|={np.abs(ratio):.4f}, phase_ratio={np.angle(ratio):.3f}")

# Now let's look at the actual SH model response at different positions
print("\n=== SH model response at 560mm, various positions ===")
model = SphericalHarmonicSpeakerModel(order=8)

positions = [
    ("On-axis (front)", CylindricalPosition(560, 0, 0)),
    ("45deg up", CylindricalPosition(560, 0, 450)),
    ("90deg side", CylindricalPosition(0, 90, 0)),
    ("Behind", CylindricalPosition(560, 180, 0)),
]

for name, pos in positions:
    H = model.compute_frequency_response(fs, pos, num_bins=num_bins)
    r, theta, phi = cylindrical_to_spherical(pos, None)
    mag_db = 20 * np.log10(np.abs(H) + 1e-12)
    
    # Get values at key frequencies
    for f in [100, 1000, 4000, 8000]:
        idx = int(f / (fs / 2) * (num_bins - 1))
        db_val = mag_db[idx]
        if f == 100:
            print(f"  {name:20s} (r={r*1000:.0f}mm, theta={np.degrees(theta):.1f}deg): ", end="")
        print(f"{f:>5d}Hz={db_val:6.1f}dB", end="")
    print()

# Check the phase of the Hankel ratio - this is critical for the sign change
print("\n=== Phase analysis of Hankel ratio (critical for sign change) ===")
r_m = 0.560
r_ref = 1.0
for f in [1000, 2000, 4000, 8000]:
    k = 2 * np.pi * f / SPEED_OF_SOUND
    kr = k * r_m
    kr_ref = k * r_ref
    print(f"\n  f={f}Hz:")
    for l in [0, 1, 2, 4, 8]:
        h_r = spherical_bessel_hankel(l, np.array([kr]))
        h_ref = spherical_bessel_hankel(l, np.array([kr_ref]))
        ratio = h_r[0] / h_ref[0] if np.abs(h_ref[0]) > 1e-12 else 1.0
        print(f"    l={l}: phase_h(r)={np.angle(h_r[0]):.3f}, phase_h(ref)={np.angle(h_ref[0]):.3f}, phase_ratio={np.angle(ratio):.3f}, |ratio|={np.abs(ratio):.4f}")

# Plot the Hankel function magnitude and phase for l=0,1,2,4,8
fig, axes = plt.subplots(2, 3, figsize=(18, 10))

for idx, l in enumerate([0, 1, 2, 4, 6, 8]):
    ax = axes[idx // 3, idx % 3]
    kr_vals = np.linspace(0.01, 100, 1000)
    h = spherical_bessel_hankel(l, kr_vals)
    
    ax.plot(kr_vals, 20 * np.log10(np.abs(h) + 1e-12))
    ax.axvline(x=l, color='red', linestyle='--', alpha=0.5, label=f'l={l} (kr=l)')
    ax.set_xlabel('kr')
    ax.set_ylabel('|h_l(kr)| (dB)')
    ax.set_title(f'Spherical Hankel 2nd kind, l={l}')
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.set_ylim(-60, 20)

plt.tight_layout()
plt.savefig('debug_hankel_behavior.png', dpi=150)
print("\nSaved: debug_hankel_behavior.png")

# Now check: what if the issue is the normalization?
# The model normalizes to r_ref=1m.  At 560mm, the ratio should be ~1.79 (5dB)
# But what if the user's post-processing expects a different normalization?
print("\n=== Normalization check ===")
print(f"Distance ratio: r_ref/r = {r_ref/r_m:.3f} = {20*np.log10(r_ref/r_m):.1f}dB")
print(f"This means at 560mm, the response should be {20*np.log10(r_ref/r_m):.1f}dB higher than at 1m")
print("(because the wave is stronger closer to the source)")
