"""Generate directivity plots from raw IR files.

Parses Dimitri's naming convention (rX_phY_zZ_ir.wav), loads all IRs,
computes their FFT magnitude, and generates horizontal and vertical
directivity spectrograms by selecting points close to each angle.
"""
import re
import math
from pathlib import Path

import numpy as np
import soundfile as sf
import matplotlib.pyplot as plt


def parse_dimitri_filename(stem: str) -> tuple:
    """Parse a Dimitri-format IR filename and return (prefix, r, phi, z).
    
    Example: 'a_million_dollars_r103p0_ph17p4_z-65p0_ir' 
    -> ('a_million_dollars', 103.0, 17.4, -65.0)
    """
    # Match pattern: prefix_r{r}_ph{ph}_z{z}_ir
    m = re.match(r'^(.*)_r(.+)_ph(.+)_z(.+)_ir$', stem)
    if not m:
        return None
    prefix = m.group(1)
    r = float(m.group(2).replace('p', '.'))
    phi = float(m.group(3).replace('p', '.'))
    z = float(m.group(4).replace('p', '.'))
    return (prefix, r, phi, z)


def load_ir_files(measurement_dir: Path) -> list:
    """Load all IR WAV files from the measurement directory.
    
    Returns a list of dicts with keys: r, phi, z, ir, fs
    """
    irs = []
    wav_files = sorted(measurement_dir.glob('*.wav'))
    
    for wf in wav_files:
        stem = wf.stem
        parsed = parse_dimitri_filename(stem)
        if parsed is None:
            continue
        
        prefix, r, phi, z = parsed
        try:
            data, fs = sf.read(wf, dtype='float32')
            # Take only the first channel if stereo
            if data.ndim > 1:
                data = data[:, 0]
            irs.append({
                'r': r,
                'phi': phi,
                'z': z,
                'ir': data,
                'fs': fs,
                'path': wf.name,
            })
        except Exception as e:
            print(f"  Warning: Could not load {wf.name}: {e}")
    
    return irs


def compute_fft_magnitude(ir: np.ndarray, fs: float) -> tuple:
    """Compute the FFT magnitude of an IR.
    
    Returns (freqs, magnitude_db) where magnitude_db is relative to the peak.
    """
    n = len(ir)
    fft = np.fft.rfft(ir)
    freqs = np.fft.rfftfreq(n, 1.0 / fs)
    magnitude = np.abs(fft)
    
    # Normalize to peak and convert to dB
    peak = np.max(magnitude)
    if peak > 1e-12:
        magnitude_db = 20 * np.log10(magnitude / peak + 1e-12)
    else:
        magnitude_db = np.zeros_like(magnitude)
    
    return (freqs, magnitude_db)


def generate_directivity_plots(irs: list, output_dir: Path = None) -> None:
    """Generate horizontal and vertical directivity spectrograms from IR data.
    
    For horizontal: selects points near z=0, uses actual phi angles.
    For vertical: selects points near phi=0, uses elevation angles from z/r.
    Interpolates between measurement points for smooth spectrograms.
    """
    if not irs:
        print("No IR data loaded.")
        return
    
    # Determine common frequency grid
    fs = irs[0]['fs']
    print(f"Sample rate: {fs} Hz")
    print(f"Number of IRs: {len(irs)}")
    
    # Compute FFT for all IRs
    print("Computing FFTs...")
    for ir_data in irs:
        freqs, mag_db = compute_fft_magnitude(ir_data['ir'], ir_data['fs'])
        ir_data['freqs'] = freqs
        ir_data['mag_db'] = mag_db
    
    # Define frequency grid for plotting (100 Hz to 20 kHz)
    freq_plot = np.linspace(100, 20000, 200)
    
    # ============================================================
    # Horizontal directivity: z ≈ 0, varying phi
    # ============================================================
    print("\nBuilding horizontal directivity plot...")
    
    # Select points close to z=0 (within ±20mm)
    h_candidates = [ir for ir in irs if abs(ir['z']) <= 20.0]
    print(f"  Points near z=0: {len(h_candidates)}")
    
    if not h_candidates:
        # Relax the constraint
        h_candidates = [ir for ir in irs if abs(ir['z']) <= 50.0]
        print(f"  Relaxed to z=±50mm: {len(h_candidates)}")
    
    # Sort by phi angle
    h_candidates.sort(key=lambda ir: ir['phi'])
    h_angles = np.array([ir['phi'] for ir in h_candidates])
    print(f"  Angle range: {h_angles[0]:.1f}° to {h_angles[-1]:.1f}°")
    
    # Build spectrogram at actual measurement angles
    n_h = len(h_candidates)
    h_spectrogram = np.zeros((n_h, len(freq_plot)))
    
    for i, ir in enumerate(h_candidates):
        h_spectrogram[i, :] = np.interp(freq_plot, ir['freqs'], ir['mag_db'])
    
    # Normalize: subtract the max at each frequency (so max = 0 dB)
    h_max_per_freq = np.max(h_spectrogram, axis=0)
    h_normalized = h_spectrogram - h_max_per_freq
    
    # Interpolate to fine angle grid for smooth plotting
    h_fine_angles = np.linspace(-180, 180, 360)
    h_fine_spectrogram = np.zeros((len(h_fine_angles), len(freq_plot)))
    for fi in range(len(freq_plot)):
        h_fine_spectrogram[:, fi] = np.interp(
            h_fine_angles, h_angles, h_normalized[:, fi],
            left=np.nan, right=np.nan
        )
    
    # ============================================================
    # Vertical directivity: phi ≈ 0, varying z -> elevation angle
    # ============================================================
    print("\nBuilding vertical directivity plot...")
    
    # Select points near phi=0 (within ±15 degrees, wrapping around)
    v_candidates = [ir for ir in irs if abs(ir['phi']) <= 15.0 or abs(ir['phi'] - 360.0) <= 15.0 or abs(ir['phi'] + 360.0) <= 15.0]
    print(f"  Points near phi=0: {len(v_candidates)}")
    
    if not v_candidates:
        # Relax the constraint
        v_candidates = [ir for ir in irs if abs(ir['phi']) <= 30.0]
        print(f"  Relaxed to phi=±30: {len(v_candidates)}")
    
    # Compute elevation angles for candidates
    # elevation = arctan2(z, r) in degrees
    for ir in v_candidates:
        ir['elevation'] = math.degrees(math.atan2(ir['z'], ir['r']))
    
    # Sort by elevation
    v_candidates.sort(key=lambda ir: ir['elevation'])
    v_elevations = np.array([ir['elevation'] for ir in v_candidates])
    print(f"  Elevation range: {v_elevations[0]:.1f}° to {v_elevations[-1]:.1f}°")
    
    # Build spectrogram at actual measurement elevations
    n_v = len(v_candidates)
    v_spectrogram = np.zeros((n_v, len(freq_plot)))
    
    for i, ir in enumerate(v_candidates):
        v_spectrogram[i, :] = np.interp(freq_plot, ir['freqs'], ir['mag_db'])
    
    # Normalize
    v_max_per_freq = np.max(v_spectrogram, axis=0)
    v_normalized = v_spectrogram - v_max_per_freq
    
    # Interpolate to fine elevation grid
    v_fine_elevations = np.linspace(-90, 90, 180)
    v_fine_spectrogram = np.zeros((len(v_fine_elevations), len(freq_plot)))
    for fi in range(len(freq_plot)):
        v_fine_spectrogram[:, fi] = np.interp(
            v_fine_elevations, v_elevations, v_normalized[:, fi],
            left=np.nan, right=np.nan
        )
    
    # ============================================================
    # Plotting
    # ============================================================
    print("\nGenerating plots...")
    
    fig, (ax_h, ax_v) = plt.subplots(2, 1, figsize=(14, 10))
    
    # Horizontal - use fine interpolated grid
    im_h = ax_h.pcolormesh(
        freq_plot, h_fine_angles, h_fine_spectrogram,
        shading='auto', cmap='jet', vmin=-40, vmax=0
    )
    ax_h.set_xscale('log')
    ax_h.set_xlim(100, 20000)
    ax_h.set_ylim(-180, 180)
    ax_h.set_xlabel('Frequency (Hz)', fontsize=12)
    ax_h.set_ylabel('Azimuth angle φ (degrees)', fontsize=12)
    ax_h.set_title('Horizontal Directivity from IRs (z ≈ 0)\n'
                   'FFT magnitude, normalized to max per frequency',
                   fontsize=13)
    ax_h.grid(True, which='both', linewidth=0.5, alpha=0.3)
    ax_h.set_xticks([100, 200, 500, 1000, 2000, 5000, 10000, 20000])
    ax_h.set_yticks(np.arange(-180, 181, 30))
    plt.colorbar(im_h, ax=ax_h, label='Relative level (dB)')
    
    # Vertical - use fine interpolated grid
    im_v = ax_v.pcolormesh(
        freq_plot, v_fine_elevations, v_fine_spectrogram,
        shading='auto', cmap='jet', vmin=-40, vmax=0
    )
    ax_v.set_xscale('log')
    ax_v.set_xlim(100, 20000)
    ax_v.set_ylim(-90, 90)
    ax_v.set_xlabel('Frequency (Hz)', fontsize=12)
    ax_v.set_ylabel('Elevation angle (degrees)', fontsize=12)
    ax_v.set_title('Vertical Directivity from IRs (φ ≈ 0°)\n'
                   'FFT magnitude, normalized to max per frequency',
                   fontsize=13)
    ax_v.grid(True, which='both', linewidth=0.5, alpha=0.3)
    ax_v.set_xticks([100, 200, 500, 1000, 2000, 5000, 10000, 20000])
    ax_v.set_yticks(np.arange(-90, 91, 30))
    plt.colorbar(im_v, ax=ax_v, label='Relative level (dB)')
    
    plt.tight_layout()
    
    # Save
    if output_dir is None:
        output_dir = Path('.')
    output_dir = Path(output_dir)
    output_path = output_dir / 'directivity_from_irs.png'
    plt.savefig(output_path, dpi=150)
    print(f"\nSaved to {output_path}")
    
    plt.close('all')
    
    # Print summary statistics
    print("\n=== Summary ===")
    print(f"Horizontal: {len(h_candidates)} IRs, angles {h_angles[0]:.1f}° to {h_angles[-1]:.1f}°")
    print(f"Vertical: {len(v_candidates)} IRs, elevations {v_elevations[0]:.1f}° to {v_elevations[-1]:.1f}°")
    
    # Check front vs back at a few frequencies
    # Find the IR closest to 0° (front) and ±180° (back)
    front_ir = min(h_candidates, key=lambda ir: abs(ir['phi']))
    back_ir = min(h_candidates, key=lambda ir: min(abs(ir['phi'] - 180), abs(ir['phi'] + 180)))
    
    test_freqs = [200, 1000, 5000, 10000]
    print(f"\nFront (phi={front_ir['phi']:.1f} deg) vs Back (phi={back_ir['phi']:.1f} deg):")
    for f in test_freqs:
        f_idx = np.argmin(np.abs(freq_plot - f))
        front_db = h_normalized[h_candidates.index(front_ir), f_idx]
        back_db = h_normalized[h_candidates.index(back_ir), f_idx]
        print(f"  {f:6d} Hz: Front={front_db:6.1f} dB, Back={back_db:6.1f} dB, Diff={front_db - back_db:+.1f} dB")


def main():
    measurement_dir = Path(r"C:\Users\User\Downloads\test\measurement_set")
    
    if not measurement_dir.exists():
        print(f"Error: Directory not found: {measurement_dir}")
        return
    
    print(f"Loading IR files from {measurement_dir}...")
    irs = load_ir_files(measurement_dir)
    
    if not irs:
        print("No IR files found.")
        return
    
    print(f"Loaded {len(irs)} IR files.")
    
    # Print some stats about the data
    phis = [ir['phi'] for ir in irs]
    zs = [ir['z'] for ir in irs]
    rs = [ir['r'] for ir in irs]
    print(f"\nData range:")
    print(f"  Radius: {min(rs):.1f} to {max(rs):.1f} mm")
    print(f"  Phi: {min(phis):.1f}° to {max(phis):.1f}°")
    print(f"  Z: {min(zs):.1f} to {max(zs):.1f} mm")
    
    generate_directivity_plots(irs, measurement_dir)


if __name__ == '__main__':
    main()
