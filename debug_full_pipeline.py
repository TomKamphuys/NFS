"""Simulate the full measurement pipeline: sweep -> model -> deconvolution -> FR."""
import numpy as np
from scipy.signal import chirp
from nfs.datatypes import CylindricalPosition
from nfs.utils.spatial import (
    SphericalHarmonicSpeakerModel,
    DiscretePistonSpeakerModel,
    SPEED_OF_SOUND,
)

fs = 48000
duration = 3  # 3 second sweep
N = int(fs * duration)

# Generate exponential sine sweep
t = np.arange(N) / fs
f1, f2 = 20, 20000
sweep = np.sin(2 * np.pi * f1 * t * (f2/f1) ** (t / duration))
sweep = sweep.astype(np.float64)

# Apply window to reduce edge artifacts
window = np.hanning(N)
sweep *= window

model = SphericalHarmonicSpeakerModel(order=8)
discrete_model = DiscretePistonSpeakerModel(piston_point_count=100)

def compute_ir_from_sweep(model_obj, pos, fs, sweep):
    """Apply model to sweep, then deconvolve to get IR."""
    # Apply model
    num_bins = len(sweep) // 2 + 1
    H = model_obj.compute_frequency_response(fs, pos, num_bins=num_bins)
    
    # FFT of sweep
    S = np.fft.rfft(sweep)
    
    # Apply frequency response
    S_filtered = S * H
    
    # Deconvolve: divide by original sweep spectrum
    # IR = FFT^{-1}{S_filtered / S} = FFT^{-1}{H}
    # But we need to handle the sweep's own spectrum
    ir_freq = np.where(np.abs(S) > 1e-12, S_filtered / S, 0)
    
    # Inverse FFT
    ir = np.fft.irfft(ir_freq, n=N)
    
    return ir, H

# Test at various distances
print("=== Full pipeline: IR energy at various distances (order=8) ===")
for d in [400, 500, 560, 680, 800, 1000]:
    pos = CylindricalPosition(d, 0, 0)
    ir, H = compute_ir_from_sweep(model, pos, fs, sweep)
    
    # Compute FR magnitude from the IR
    ir_fft = np.fft.rfft(ir)
    fr_db = 20 * np.log10(np.abs(ir_fft) + 1e-12)
    
    # Also compute directly from H
    h_db = 20 * np.log10(np.abs(H) + 1e-12)
    
    for f_hz in [100, 1000, 4000, 8000]:
        idx = int(f_hz / (fs / 2) * (len(H) - 1))
        if f_hz == 100:
            print(f"  r={d}mm: ", end="")
        print(f"{f_hz//1000}k={h_db[idx]:5.1f}", end=" ")
    print()

# Now simulate what the user likely does: measures at multiple points on a spherical shell,
# then averages the FRs
print("\n=== Averaged FR over spherical shell at various distances ===")
for dist_mm in [560, 680, 800]:
    # Generate points on a spherical shell
    fr_sum = np.zeros(fs // 2 + 1, dtype=np.complex128)
    count = 0
    
    for z_mm in np.linspace(-300, 300, 7):
        r_cyl = np.sqrt(dist_mm**2 - z_mm**2)
        if r_cyl < 10:  # Skip if too close to axis
            continue
        pos = CylindricalPosition(r_cyl, 0, z_mm)
        H = model.compute_frequency_response(fs, pos, num_bins=fs // 2 + 1)
        fr_sum += H
        count += 1
    
    if count > 0:
        fr_avg = fr_sum / count
        fr_db = 20 * np.log10(np.abs(fr_avg) + 1e-12)
        print(f"\n  Distance {dist_mm}mm ({count} points averaged):")
        for f_hz in [100, 500, 1000, 2000, 4000, 8000]:
            idx = int(f_hz / (fs / 2) * (fs // 2))
            print(f"    {f_hz//1000}kHz={fr_db[idx]:5.1f}dB", end=" ")
        print()

# Compare with discrete piston
print("\n=== Discrete piston at same positions ===")
for d in [400, 560, 680, 800]:
    pos = CylindricalPosition(d, 0, 0)
    H = discrete_model.compute_frequency_response(fs, pos, num_bins=fs // 2 + 1)
    h_db = 20 * np.log10(np.abs(H) + 1e-12)
    print(f"  r={d}mm: ", end="")
    for f_hz in [100, 1000, 4000, 8000]:
        idx = int(f_hz / (fs / 2) * (len(H) - 1))
        print(f"{f_hz//1000}k={h_db[idx]:5.1f}", end=" ")
    print()
