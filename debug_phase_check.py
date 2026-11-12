"""Verify phase behavior and impulse response after Hankel 2nd kind switch."""
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

print("=== Phase behavior of Hankel ratio ===")
print("(Positive phase = signal leads reference = correct for r < r_ref)\n")

r_ref = 1.0
for r_mm in [400, 560, 680, 800]:
    r_m = r_mm / 1000.0
    k = 2.0 * np.pi * freqs / SPEED_OF_SOUND
    kr = k * r_m
    kr_ref = k * r_ref
    
    h_r = spherical_bessel_hankel(0, kr)
    h_ref = spherical_bessel_hankel(0, kr_ref)
    ratio = np.where(np.abs(h_ref) > 1e-12, h_r / h_ref, 1.0)
    
    # Check phase at key frequencies
    print(f"r = {r_mm}mm:")
    for f in [100, 1000, 4000, 8000]:
        idx = int(f / (fs / 2) * (num_bins - 1))
        phase_deg = np.degrees(np.angle(ratio[idx]))
        mag_db = 20 * np.log10(np.abs(ratio[idx]) + 1e-12)
        # Expected time delay
        expected_delay_us = (r_ref - r_m) / SPEED_OF_SOUND * 1e6
        expected_phase_deg = -360 * f * expected_delay_us / 1e6
        print(f"  {f:>5d} Hz: phase={phase_deg:7.1f} deg, |ratio|={mag_db:5.1f}dB "
              f"(expected phase ~{expected_phase_deg:7.1f} deg for delay {expected_delay_us:.0f} us)")
    print()

print("=== Impulse response verification ===")
print("(Peak should be at positive time = correct outgoing wave)\n")

# Generate a short exponential sweep
duration = 1.0
t = np.linspace(0, duration, int(fs * duration), endpoint=False)
# Exponential sweep: f(t) = f1 * (f2/f1)^(t/T)
f1, f2 = 20, 20000
T = duration
sweep_freqs = f1 * (f2 / f1) ** (t / T)
sweep_phase = 2 * np.pi * (f1 * T / np.log(f2 / f1)) * ((f2 / f1) ** (t / T) - 1)
sweep = np.sin(sweep_phase).astype(np.float64)

# Apply SH model at different distances
model = SphericalHarmonicSpeakerModel(order=8)
for r_mm in [560, 680, 800]:
    pos = CylindricalPosition(r_mm, 0.0, 0.0)
    filtered = model.apply_to_signal(sweep, fs, pos)
    
    # Deconvolve: divide FFT of filtered by FFT of original sweep
    S_filtered = np.fft.rfft(filtered)
    S_orig = np.fft.rfft(sweep)
    # Avoid division by zero
    mask = np.abs(S_orig) > 1e-12
    H_ir = np.zeros_like(S_orig, dtype=np.complex128)
    H_ir[mask] = S_filtered[mask] / S_orig[mask]
    ir = np.fft.irfft(H_ir, n=len(sweep))
    
    # Find peak
    peak_idx = np.argmax(np.abs(ir))
    peak_time_ms = peak_idx / fs * 1000
    
    # Expected time delay
    expected_delay_ms = (r_ref - r_mm / 1000.0) / SPEED_OF_SOUND * 1000
    
    print(f"r = {r_mm}mm: IR peak at {peak_time_ms:.1f}ms "
          f"(expected {expected_delay_ms:.1f}ms), "
          f"peak value = {ir[peak_idx]:.4f}")

print("\n=== FR magnitude comparison (Hankel 1st vs 2nd kind) ===")
print("(Magnitudes should be IDENTICAL - they are complex conjugates)\n")

from scipy.special import spherical_jn, spherical_yn

kr_test = np.linspace(0.01, 50, 100)
h1 = spherical_jn(0, kr_test) + 1j * spherical_yn(0, np.where(kr_test < 1e-12, 1e-12, kr_test))
h2 = spherical_jn(0, kr_test) - 1j * spherical_yn(0, np.where(kr_test < 1e-12, 1e-12, kr_test))

mag_diff = np.max(np.abs(np.abs(h1) - np.abs(h2)))
# Phase of h1 should be negative of phase of h2 (complex conjugates)
phase_sum = np.angle(h1) + np.angle(h2)
print(f"Max magnitude difference |h1| - |h2|: {mag_diff:.2e} (should be ~0)")
print(f"h1 is complex conjugate of h2: {np.allclose(h1, np.conj(h2))}")

# Verify FR magnitude is same for both kinds
print("\n=== Full FR magnitude at 560mm (both kinds) ===")

pos = CylindricalPosition(560, 0.0, 0.0)
H_current = model.compute_frequency_response(fs, pos, num_bins=num_bins)

# Manually compute with Hankel 1st kind for comparison
k = 2.0 * np.pi * freqs / SPEED_OF_SOUND
r_m = 0.560
kr = k * r_m
kr_ref = k * r_ref

# Use l=0 only for simplicity (on-axis, dominant term)
h1_r = spherical_jn(0, kr) + 1j * spherical_yn(0, np.where(kr < 1e-12, 1e-12, kr))
h1_ref = spherical_jn(0, kr_ref) + 1j * spherical_yn(0, np.where(kr_ref < 1e-12, 1e-12, kr_ref))
ratio1 = np.where(np.abs(h1_ref) > 1e-12, h1_r / h1_ref, 1.0)

h2_r = spherical_jn(0, kr) - 1j * spherical_yn(0, np.where(kr < 1e-12, 1e-12, kr))
h2_ref = spherical_jn(0, kr_ref) - 1j * spherical_yn(0, np.where(kr_ref < 1e-12, 1e-12, kr_ref))
ratio2 = np.where(np.abs(h2_ref) > 1e-12, h2_r / h2_ref, 1.0)

mag1_db = 20 * np.log10(np.abs(ratio1) + 1e-12)
mag2_db = 20 * np.log10(np.abs(ratio2) + 1e-12)
phase1_deg = np.degrees(np.angle(ratio1))
phase2_deg = np.degrees(np.angle(ratio2))

print(f"Max magnitude difference (dB): {np.max(np.abs(mag1_db - mag2_db)):.2e}")
print(f"Phase 1st kind = -Phase 2nd kind: {np.allclose(phase1_deg, -phase2_deg)}")

for f in [100, 500, 1000, 4000, 8000]:
    idx = int(f / (fs / 2) * (num_bins - 1))
    print(f"  {f:>5d} Hz: |ratio1|={mag1_db[idx]:6.2f}dB, |ratio2|={mag2_db[idx]:6.2f}dB, "
          f"phase1={phase1_deg[idx]:7.1f}°, phase2={phase2_deg[idx]:7.1f}°")

# Plot
fig, axes = plt.subplots(2, 2, figsize=(14, 10))

# Plot 1: Phase of Hankel ratio vs frequency
ax1 = axes[0, 0]
for r_mm in [400, 560, 680, 800]:
    r_m = r_mm / 1000.0
    kr = k * r_m
    kr_ref = k * r_ref
    h_r = spherical_bessel_hankel(0, kr)
    h_ref = spherical_bessel_hankel(0, kr_ref)
    ratio = np.where(np.abs(h_ref) > 1e-12, h_r / h_ref, 1.0)
    phase_deg = np.degrees(np.angle(ratio))
    mask = freqs <= 8000
    ax1.plot(freqs[mask] / 1000, phase_deg[mask], label=f'r={r_mm}mm')
    
    # Expected phase for simple time delay
    expected_delay = (r_ref - r_m) / SPEED_OF_SOUND
    expected_phase = -360 * freqs * expected_delay
    ax1.plot(freqs[mask] / 1000, expected_phase[mask], '--', alpha=0.3)

ax1.set_xlabel('Frequency (kHz)')
ax1.set_ylabel('Phase (degrees)')
ax1.set_title('Hankel ratio phase (l=0) vs expected time delay')
ax1.legend(fontsize=8)
ax1.grid(True, alpha=0.3)

# Plot 2: Magnitude of Hankel ratio vs frequency
ax2 = axes[0, 1]
for r_mm in [400, 560, 680, 800]:
    r_m = r_mm / 1000.0
    kr = k * r_m
    kr_ref = k * r_ref
    h_r = spherical_bessel_hankel(0, kr)
    h_ref = spherical_bessel_hankel(0, kr_ref)
    ratio = np.where(np.abs(h_ref) > 1e-12, h_r / h_ref, 1.0)
    mag_db = 20 * np.log10(np.abs(ratio) + 1e-12)
    mask = freqs <= 8000
    ax2.plot(freqs[mask] / 1000, mag_db[mask], label=f'r={r_mm}mm')
    
    # Expected magnitude: r_ref / r (far-field)
    expected_db = 20 * np.log10(r_ref / r_m)
    ax2.axhline(y=expected_db, linestyle='--', alpha=0.3)

ax2.set_xlabel('Frequency (kHz)')
ax2.set_ylabel('Magnitude (dB)')
ax2.set_title('Hankel ratio magnitude (l=0) vs far-field expectation')
ax2.legend(fontsize=8)
ax2.grid(True, alpha=0.3)

# Plot 3: Impulse response
ax3 = axes[1, 0]
for r_mm in [560, 680, 800]:
    pos = CylindricalPosition(r_mm, 0.0, 0.0)
    filtered = model.apply_to_signal(sweep, fs, pos)
    S_filtered = np.fft.rfft(filtered)
    S_orig = np.fft.rfft(sweep)
    mask = np.abs(S_orig) > 1e-12
    H_ir = np.zeros_like(S_orig, dtype=np.complex128)
    H_ir[mask] = S_filtered[mask] / S_orig[mask]
    ir = np.fft.irfft(H_ir, n=len(sweep))
    
    # Show first 50ms of IR
    t_ms = np.arange(len(ir)) / fs * 1000
    t_mask = t_ms <= 50
    ax3.plot(t_ms[t_mask], ir[t_mask], label=f'r={r_mm}mm')
    
    expected_delay_ms = (r_ref - r_mm / 1000.0) / SPEED_OF_SOUND * 1000
    ax3.axvline(x=expected_delay_ms, linestyle='--', alpha=0.3)

ax3.set_xlabel('Time (ms)')
ax3.set_ylabel('IR amplitude')
ax3.set_title('Deconvolved impulse response (first 50ms)')
ax3.legend(fontsize=8)
ax3.grid(True, alpha=0.3)

# Plot 4: Full SH model FR magnitude at various distances
ax4 = axes[1, 1]
for r_mm in [400, 500, 560, 600, 680, 800, 1000]:
    pos = CylindricalPosition(r_mm, 0.0, 0.0)
    H = model.compute_frequency_response(fs, pos, num_bins=num_bins)
    mag_db = 20 * np.log10(np.abs(H) + 1e-12)
    mask = freqs <= 8000
    ax4.plot(freqs[mask] / 1000, mag_db[mask], label=f'r={r_mm}mm')

ax4.set_xlabel('Frequency (kHz)')
ax4.set_ylabel('Magnitude (dB)')
ax4.set_title('SH model on-axis FR at various distances (order=8)')
ax4.legend(fontsize=7)
ax4.grid(True, alpha=0.3)
ax4.set_ylim(-40, 15)

plt.tight_layout()
plt.savefig('debug_phase_and_ir.png', dpi=150)
print("\nSaved: debug_phase_and_ir.png")
