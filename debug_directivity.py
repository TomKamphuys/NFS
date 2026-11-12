"""Debug script to visualize speaker directivity using SH evaluation only.

Evaluates the spherical harmonic series directly at each (theta, phi) and frequency:
    D(theta, phi, f) = Σₗₘ aₗₘ(f) · Yₗₘ(theta, phi)
No radial propagation — just angular directivity. Fast enough for pcolor plots.
"""
import numpy as np
import math
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from nfs.utils.spatial import (
    SphericalHarmonicSpeakerModel,
    real_spherical_harmonics,
    cylindrical_to_spherical,
)
from nfs.datatypes import CylindricalPosition


def evaluate_sh_directivity(model, fs, theta, phi):
    """Evaluate the angular directivity at (theta, phi) for all frequencies.
    
    Returns the complex SH sum Σ aₗₘ(f) · Yₗₘ(theta, phi), normalized so
    the front-of-speaker (theta=90°, phi=0°) has unity magnitude.
    """
    num_bins = model._freq_bins
    sh = real_spherical_harmonics(model._order, theta, phi)
    
    D = np.zeros(num_bins, dtype=np.complex128)
    for (l, m), a_lm in model._coeffs.items():
        y_val = sh.get((l, m), 0.0)
        D += a_lm * y_val
    
    # Normalize by front-of-speaker response
    sh_front = real_spherical_harmonics(model._order, math.pi / 2.0, 0.0)
    D_front = np.zeros(num_bins, dtype=np.complex128)
    for (l, m), a_lm in model._coeffs.items():
        y_val = sh_front.get((l, m), 0.0)
        D_front += a_lm * y_val
    
    norm = np.abs(D_front)
    safe_norm = np.where(norm > 1e-12, norm, 1.0)
    D = np.where(norm > 1e-12, D / safe_norm, D)
    
    return D


def main():
    t0 = time.time()
    model = SphericalHarmonicSpeakerModel(order=4)
    fs = 48000
    num_bins = model._freq_bins

    # Pre-compute the normalized SH directivity at the front for reference
    print("=" * 80)
    print("Speaker Directivity Debug — SH evaluation only (no radial propagation)")
    print("=" * 80)

    # ============================================================
    # 1. Coordinate mapping check
    # ============================================================
    print("\nCoordinate mapping check")
    print("-" * 60)

    check_positions = [
        ("Front (cyl theta=0, z=0)", CylindricalPosition(100.0, 0.0, 0.0)),
        ("Back (cyl theta=180, z=0)", CylindricalPosition(100.0, 180.0, 0.0)),
        ("Right (cyl theta=90, z=0)", CylindricalPosition(100.0, 90.0, 0.0)),
        ("Left (cyl theta=-90, z=0)", CylindricalPosition(100.0, -90.0, 0.0)),
        ("Top (r=0, z=100)", CylindricalPosition(0.0, 0.0, 100.0)),
        ("Bottom (r=0, z=-100)", CylindricalPosition(0.0, 0.0, -100.0)),
    ]

    for label, pos in check_positions:
        r_m, theta, phi = cylindrical_to_spherical(pos)
        x = r_m * 1000 * math.sin(theta) * math.cos(phi)
        y = r_m * 1000 * math.sin(theta) * math.sin(phi)
        z = r_m * 1000 * math.cos(theta)
        print(f"  {label:35s}: theta={np.degrees(theta):6.1f}°  phi={np.degrees(phi):6.1f}°  -> x={x:5.1f}, y={y:5.1f}, z={z:5.1f}")

    # ============================================================
    # 2. Directivity at key positions (SH only)
    # ============================================================
    print("\nDirectivity at key positions (SH only, normalized to front)")
    print("-" * 60)

    test_freqs = [200, 500, 1000, 2000, 5000, 10000, 15000]
    
    # Cache the full frequency response for each check position
    cached = {}
    for label, pos in check_positions:
        r_m, theta, phi = cylindrical_to_spherical(pos)
        D = evaluate_sh_directivity(model, fs, theta, phi)
        cached[label] = D

    for freq in test_freqs:
        bin_idx = int(freq / (fs / 2) * (num_bins - 1))
        print(f"\n  {freq} Hz:")
        for label in cached:
            mag = np.abs(cached[label][bin_idx])
            print(f"    {label:35s}: |D| = {mag:.4f}")

    # ============================================================
    # 3. Raw piston vs SH reconstruction at 5000 Hz
    # ============================================================
    print("\n" + "=" * 80)
    print("Raw piston directivity vs SH reconstruction at 5000 Hz")
    print("=" * 80)

    from scipy.special import j1

    SPEED_OF_SOUND = 343.0
    piston_radius_m = 0.1016

    freq = 5000
    ka = 2 * math.pi * freq * piston_radius_m / SPEED_OF_SOUND

    alphas_rad = np.radians(np.linspace(0, 180, 181))
    sin_alpha = np.sin(alphas_rad)
    x_piston = ka * sin_alpha
    D_baffled = np.ones_like(x_piston)
    nonzero = x_piston > 1e-12
    D_baffled[nonzero] = 2.0 * j1(x_piston[nonzero]) / x_piston[nonzero]
    D_free = D_baffled * (1.0 + np.cos(alphas_rad)) / 2.0

    # Normalize D_free so front = 1
    D_free_norm = D_free / (D_free[0] + 1e-12)

    print(f"\n  ka = {ka:.3f}")
    print(f"  {'Alpha':>8s}  {'D_free':>10s}  {'SH_recon':>10s}  {'Error':>10s}")
    
    bin_idx_5k = int(freq / (fs / 2) * (num_bins - 1))
    
    for alpha_deg in [0, 15, 30, 45, 60, 75, 90, 105, 120, 135, 150, 165, 180]:
        # Alpha from +x axis: theta_spherical = 90 - alpha, phi_spherical = 0
        theta_check = math.radians(90 - alpha_deg)
        phi_check = 0.0
        
        D_sh = evaluate_sh_directivity(model, fs, theta_check, phi_check)
        sh_mag = np.abs(D_sh[bin_idx_5k])
        
        idx = int(alpha_deg)
        d_free = D_free_norm[idx]
        error = sh_mag - d_free
        
        print(f"  {alpha_deg:6d}°  {d_free:10.4f}  {sh_mag:10.4f}  {error:+10.4f}")

    # ============================================================
    # 4. Horizontal directivity scan (z=0) — fast SH evaluation
    # ============================================================
    print("\n" + "=" * 80)
    print("Horizontal directivity scan (z=0, varying cylindrical theta)")
    print("=" * 80)

    # At z=0, cylindrical theta maps to spherical phi
    # theta_spherical = 90° (equator), phi_spherical = cylindrical_theta
    n_angle = 360
    angles_deg = np.linspace(0, 360, n_angle, endpoint=False)
    
    # Pre-compute SH directivity for all angles at once
    D_horizontal = np.zeros((n_angle, num_bins), dtype=np.complex128)
    for ai, angle in enumerate(angles_deg):
        theta_sph = math.pi / 2.0  # equator (z=0)
        phi_sph = math.radians(angle)
        D_col = evaluate_sh_directivity(model, fs, theta_sph, phi_sph)
        D_horizontal[ai, :] = D_col

    mag_horizontal = np.abs(D_horizontal)

    for freq in test_freqs:
        bin_idx = int(freq / (fs / 2) * (num_bins - 1))
        mags = mag_horizontal[:, bin_idx]
        front = mags[0]
        back = mags[180]
        right = mags[90]
        left = mags[270]
        print(f"  {freq} Hz: front={front:.4f}, back={back:.4f}, "
              f"right={right:.4f}, left={left:.4f}, right/left={right/(left+1e-12):.4f}")

    # ============================================================
    # 5. Vertical directivity scan (theta=0, varying z)
    # ============================================================
    print("\n" + "=" * 80)
    print("Vertical directivity scan (cyl theta=0, varying z)")
    print("=" * 80)

    # At cylindrical theta=0, varying z:
    # x = r, y = 0, z = z
    # theta_spherical = acos(z / sqrt(r^2 + z^2)), phi_spherical = 0
    n_z = 151
    z_values = np.linspace(-150.0, 150.0, n_z)
    r_fixed = 100.0  # mm

    D_vertical = np.zeros((n_z, num_bins), dtype=np.complex128)
    for zi, z in enumerate(z_values):
        r_total = math.sqrt(r_fixed**2 + z**2) / 1000.0  # meters
        z_m = z / 1000.0
        theta_sph = math.acos(max(-1.0, min(1.0, z_m / (r_total + 1e-12))))
        phi_sph = 0.0
        D_col = evaluate_sh_directivity(model, fs, theta_sph, phi_sph)
        D_vertical[zi, :] = D_col

    mag_vertical = np.abs(D_vertical)

    z_check = [-100, -50, 0, 50, 100]
    for freq in test_freqs:
        bin_idx = int(freq / (fs / 2) * (num_bins - 1))
        print(f"  {freq} Hz:", end=" ")
        parts = []
        for z in z_check:
            zi = int((z + 150) / 300 * (n_z - 1))
            mag = mag_vertical[zi, bin_idx]
            parts.append(f"z{z:+d}={mag:.4f}")
        print(", ".join(parts))

    # ============================================================
    # 6. Generate pcolor plots
    # ============================================================
    print(f"\n  Elapsed so far: {time.time() - t0:.1f}s")
    print("  Generating pcolor plots...")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  matplotlib not available, skipping plots")
        return

    freq_grid = np.linspace(100, 20000, 200)

    # Horizontal pcolor: angle vs frequency
    mag_h_db = 20 * np.log10(np.clip(mag_horizontal, 1e-3, None))
    # Interpolate to freq_grid
    orig_freqs = np.linspace(0, fs / 2, num_bins)
    mag_h_interp = np.zeros((len(freq_grid), n_angle))
    for fi, freq in enumerate(freq_grid):
        bin_idx = int(freq / (fs / 2) * (num_bins - 1))
        mag_h_interp[fi, :] = mag_horizontal[:, bin_idx]

    mag_h_db_interp = 20 * np.log10(np.clip(mag_h_interp, 1e-3, None))
    mag_h_db_norm = mag_h_db_interp - mag_h_db_interp.max(axis=1, keepdims=True)

    fig, ax = plt.subplots(figsize=(14, 7))
    im = ax.pcolormesh(angles_deg, freq_grid, mag_h_db_norm, shading="auto",
                       cmap="RdBu_r", vmin=-60, vmax=0)
    ax.set_xlabel("Cylindrical θ (degrees)", fontsize=12)
    ax.set_ylabel("Frequency (Hz)", fontsize=12)
    ax.set_title("Horizontal Directivity (z=0, r=100mm)\nSH only, normalized to max per frequency",
                 fontsize=13)
    ax.set_xlim(0, 360)
    ax.set_ylim(100, 20000)
    ax.set_yscale("log")
    ax.set_xticks([0, 45, 90, 135, 180, 225, 270, 315])
    ax.set_xticklabels(["0°", "45°", "90°", "135°", "180°", "225°", "270°", "315°"])
    plt.colorbar(im, ax=ax, label="Magnitude (dB re max)")
    plt.tight_layout()
    plt.savefig("debug_horizontal_pcolor.png", dpi=150, bbox_inches="tight")
    print("  Saved: debug_horizontal_pcolor.png")

    # Vertical pcolor: z vs frequency
    mag_v_interp = np.zeros((len(freq_grid), n_z))
    for fi, freq in enumerate(freq_grid):
        bin_idx = int(freq / (fs / 2) * (num_bins - 1))
        mag_v_interp[fi, :] = mag_vertical[:, bin_idx]

    mag_v_db_interp = 20 * np.log10(np.clip(mag_v_interp, 1e-3, None))
    mag_v_db_norm = mag_v_db_interp - mag_v_db_interp.max(axis=1, keepdims=True)

    fig, ax = plt.subplots(figsize=(14, 7))
    im = ax.pcolormesh(z_values, freq_grid, mag_v_db_norm, shading="auto",
                       cmap="RdBu_r", vmin=-60, vmax=0)
    ax.set_xlabel("Z position (mm)", fontsize=12)
    ax.set_ylabel("Frequency (Hz)", fontsize=12)
    ax.set_title("Vertical Directivity (cyl θ=0°, r=100mm)\nSH only, normalized to max per frequency",
                 fontsize=13)
    ax.set_ylim(100, 20000)
    ax.set_yscale("log")
    plt.colorbar(im, ax=ax, label="Magnitude (dB re max)")
    plt.tight_layout()
    plt.savefig("debug_vertical_pcolor.png", dpi=150, bbox_inches="tight")
    print("  Saved: debug_vertical_pcolor.png")

    print(f"\n  Total elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
