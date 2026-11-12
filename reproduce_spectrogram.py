"""Reproduce the post-processor's horizontal spectrogram from virtual IRs.

Loads all IRs near z=0, computes FFT magnitude, and plots a spectrogram
matching the post-processor's format (angle on y-axis, frequency on x-axis).
"""
import re
import math
from pathlib import Path

import numpy as np
import soundfile as sf
import matplotlib.pyplot as plt


def parse_filename(stem: str):
    """Parse Dimitri-format filename and return (r, phi, z)."""
    m = re.match(r'^.*_r(.+)_ph(.+)_z(.+)_ir$', stem)
    if not m:
        return None
    r = float(m.group(1).replace('p', '.'))
    phi = float(m.group(2).replace('p', '.'))
    z = float(m.group(3).replace('p', '.'))
    return (r, phi, z)


def main():
    measurement_dir = Path(r"C:\Users\User\Downloads\test\measurement_set")
    
    # Load all IRs
    irs = []
    for wf in sorted(measurement_dir.glob('*_ir.wav')):
        parsed = parse_filename(wf.stem)
        if parsed is None:
            continue
        r, phi, z = parsed
        try:
            data, fs = sf.read(wf, dtype='float32')
            if data.ndim > 1:
                data = data[:, 0]
            irs.append({
                'r': r, 'phi': phi, 'z': z,
                'ir': data, 'fs': fs, 'name': wf.name,
            })
        except Exception as e:
            print(f"Warning: Could not load {wf.name}: {e}")
    
    print(f"Loaded {len(irs)} IRs")
    
    # Select points near z=0 for horizontal plot
    h_irs = [ir for ir in irs if abs(ir['z']) <= 30]
    h_irs.sort(key=lambda ir: ir['phi'])
    print(f"Points near z=0: {len(h_irs)}")
    
    # Compute FFT for each
    freqs = None
    mag_dbs = []
    angles = []
    
    for ir in h_irs:
        fft = np.fft.rfft(ir['ir'])
        mag = np.abs(fft)
        f = np.fft.rfftfreq(len(ir['ir']), 1.0 / ir['fs'])
        
        if freqs is None:
            freqs = f
        
        # Normalize to peak and convert to dB
        peak = np.max(mag)
        if peak > 1e-12:
            mag_db = 20 * np.log10(mag / peak + 1e-12)
        else:
            mag_db = np.zeros_like(mag)
        
        mag_dbs.append(mag_db)
        angles.append(ir['phi'])
    
    angles = np.array(angles)
    mag_dbs = np.array(mag_dbs)  # shape: (n_points, n_freqs)
    
    # Filter to 100 Hz - 20 kHz
    freq_mask = (freqs >= 100) & (freqs <= 20000)
    freqs_plot = freqs[freq_mask]
    mag_dbs_plot = mag_dbs[:, freq_mask]
    
    # Interpolate to fine angle grid
    fine_angles = np.linspace(-180, 180, 361)
    mag_dbs_fine = np.zeros((len(fine_angles), len(freqs_plot)))
    
    for fi in range(len(freqs_plot)):
        mag_dbs_fine[:, fi] = np.interp(
            fine_angles, angles, mag_dbs_plot[:, fi],
            left=np.nan, right=np.nan
        )
    
    # Plot
    fig, ax = plt.subplots(figsize=(16, 6))
    
    im = ax.pcolormesh(
        freqs_plot, fine_angles, mag_dbs_fine,
        shading='auto', cmap='jet', vmin=-40, vmax=0
    )
    ax.set_xscale('log')
    ax.set_xlim(100, 20000)
    ax.set_ylim(-180, 180)
    ax.set_xlabel('Frequency / Hz', fontsize=14)
    ax.set_ylabel('Angle / degrees', fontsize=14)
    ax.set_title('Horizontal Directivity from Virtual IRs (z ≈ 0)\n'
                 'FFT magnitude normalized to peak per IR, relative level',
                 fontsize=15)
    ax.grid(True, which='both', linewidth=0.5, alpha=0.3)
    ax.set_xticks([100, 200, 500, 1000, 2000, 5000, 10000, 20000])
    ax.set_yticks(np.arange(-150, 151, 30))
    plt.colorbar(im, ax=ax, label='Relative level (dB)')
    
    plt.tight_layout()
    output_path = measurement_dir / 'horizontal_spectrogram_repro.png'
    plt.savefig(output_path, dpi=150)
    print(f"Saved to {output_path}")
    
    # Print energy at key angles
    print("\n=== Energy at key angles (5-10 kHz band) ===")
    hf_mask = (freqs_plot >= 5000) & (freqs_plot <= 10000)
    for angle_name, angle_val in [('Front (0°)', 0), ('Side (90°)', 90), ('Back (170°)', 170)]:
        idx = np.argmin(np.abs(angles - angle_val))
        avg_db = np.mean(mag_dbs_plot[idx, hf_mask])
        print(f"  {angle_name}: {avg_db:.1f} dB (file: {h_irs[idx]['name']})")


if __name__ == '__main__':
    main()
