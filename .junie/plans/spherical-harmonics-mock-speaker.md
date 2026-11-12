---
sessionId: session-261028-214003-9v0o
---

# Requirements

### Overview
Enhance `MockInterfaceAudio` so that instead of returning a flat impulse response for the Farina sweep, it simulates the captured sound of a virtual reference speaker at the current measurement position. The simulation uses **spherical harmonics** for the angular (directional) response and **spherical wave functions** (Bessel/Hankel) for the radial distance dependence.

### Scope

**In Scope:**
- New `SphericalHarmonicSpeakerModel` class that computes a complex frequency response H(f, r, θ, φ) given a measurement position.
- Angular part: real spherical harmonics Yₗₘ(θ, φ) weighted by frequency-dependent coefficients aₗₘ(f).
- Radial part: spherical wave propagation using Bessel/Hankel functions (near-field + far-field).
- Integration into `MockInterfaceAudio._run_sweep()` so the captured mic signal is filtered by the spatial response.
- A position callback mechanism so the mock can query the current measurement position without coupling to the scanner.
- Default coefficients for a simple reference speaker (a few non-zero coefficients, e.g., omnidirectional + dipole).

**Out of Scope:**
- Loading measured spherical harmonic coefficients from external data files.
- GUI configuration of speaker model parameters.
- Changes to the hardware audio path (`Audio` class).
- Spherical measurement point support beyond cylindrical positions (the model works with any position, but the callback provides `CylindricalPosition`).

### Key Behaviors
- At on-axis position (θ=0), the response should be nearly flat (like the current mock) for a simple omnidirectional model.
- Off-axis positions should show directional attenuation based on the spherical harmonic coefficients.
- Closer radial distances should show near-field effects (pressure variation) via the spherical wave function.
- The loopback reference channel remains unchanged (no spatial response applied).

# Technical Design

### Current Implementation

`MockInterfaceAudio` (line 1884–2010 in `audio.py`) overrides `_run_sweep()` to generate a Farina sweep, apply hardware simulation (FIR filter, HPF, 20ms delay, noise), and return the loopback signal. The critical line is:

```python
rec_mic = apply_hardware_sim(tx_sweep)  # line 1974
```

This applies only hardware effects — no spatial or directional response. The class has no concept of measurement position.

### Key Decisions

1. **Position callback**: A `Callable[[], CylindricalPosition]` is passed to `MockInterfaceAudio` (via `AudioFactory`), allowing the mock to query the current position without coupling to `Scanner`. The `NearFieldScanner` provides the callback.

2. **Separate model class**: `SphericalHarmonicSpeakerModel` in `src/nfs/utils/spatial.py` encapsulates all spatial acoustics logic, keeping `audio.py` clean and the model testable independently.

3. **Frequency-domain application**: The spatial response H(f) is computed as a complex frequency response and applied via FFT multiplication on the sweep signal before the hardware simulation.

4. **Default coefficients**: A simple model with a₀₀ (omnidirectional) = 1.0 and a₁₀ (dipole along z-axis) = 0.3 provides directional variation without requiring measured data.

### Proposed Changes

#### 1. New file: `src/nfs/utils/spatial.py`

**`SphericalHarmonicSpeakerModel` class:**

- `__init__(order: int = 2, coeffs: Optional[Dict[Tuple[int,int], np.ndarray]] = None)`: Initialize with SH order and optional frequency-dependent coefficients. If none provided, uses default simple model.
- `compute_frequency_response(fs: int, position: CylindricalPosition, speaker_origin: CylindricalPosition = None) -> np.ndarray`: Returns complex frequency response H(f) of length `fs//2 + 1`.
  - Converts cylindrical position to spherical coordinates (r, θ, φ) relative to speaker origin.
  - Evaluates real spherical harmonics Yₗₘ(θ, φ) up to the specified order.
  - Computes radial propagation using spherical Hankel functions: hₗ(kr) = jₗ(kr) + i·yₗ(kr), where k = 2πf/c.
  - Combines: H(f) = Σₗₘ aₗₘ(f) · Yₗₘ(θ, φ) · radial_factor(f, r).
- `apply_to_signal(signal: np.ndarray, fs: int, position: CylindricalPosition) -> np.ndarray`: Applies the frequency response to a time-domain signal via FFT.

**Helper functions:**
- `real_spherical_harmonics(order: int, theta: float, phi: float) -> Dict[Tuple[int,int], float]`: Computes real SH basis functions.
- `spherical_bessel_hankel(l: int, kr: np.ndarray) -> np.ndarray`: Computes spherical Bessel/Hankel functions for radial propagation.
- `cylindrical_to_spherical(pos: CylindricalPosition, origin: CylindricalPosition) -> Tuple[float, float, float]`: Converts to (r, θ, φ) in meters.

#### 2. Modified: `src/nfs/audio.py`

**`MockInterfaceAudio` class:**
- New constructor parameter: `position_callback: Optional[Callable[[], CylindricalPosition]] = None`.
- New constructor parameter: `speaker_model: Optional[SphericalHarmonicSpeakerModel] = None`.
- In `_run_sweep()`, before `apply_hardware_sim(tx_sweep)`, if a speaker model and position callback are available:
  ```python
  if self.speaker_model and self.position_callback:
      pos = self.position_callback()
      rec_mic = self.speaker_model.apply_to_signal(tx_sweep, self.hw['fs'], pos)
  else:
      rec_mic = tx_sweep.copy()
  rec_mic = apply_hardware_sim(rec_mic)
  ```

**`AudioFactory.create()` method:**
- When creating `MockInterfaceAudio`, passes `None` for position callback by default (backward compatible).
- New optional config key `[sweep] spatial_model` to enable/disable the spatial model in mock mode.

#### 3. Modified: `src/nfs/nfs.py`

**`NearFieldScanner` class:**
- When `take_measurement_set()` or `take_single_measurement()` is called, and the audio instance is `MockInterfaceAudio`, the scanner sets up a position callback that returns the current scanner position.
- This is done by calling a new `set_position_callback()` method on the audio instance before each capture.

#### 4. New test file: `tests/test_spatial_model.py`

- Test `SphericalHarmonicSpeakerModel` with default coefficients.
- Verify on-axis response is nearly flat.
- Verify off-axis response shows directional variation.
- Verify near-field vs far-field radial dependence.
- Test integration with `MockInterfaceAudio` via position callback.

### Architecture Diagram

```
graph LR
    NFS[NearFieldScanner] -->|position callback| MIA[MockInterfaceAudio]
    NFS -->|capture_ir| MIA
    MIA -->|query position| NFS
    MIA -->|apply spatial response| SHM[SphericalHarmonicSpeakerModel]
    SHM -->|use helpers| SPAT[spatial.py helpers]
    SCN[Scanner] -->|get_position| NFS
```

### Risks
- **Spherical Bessel functions at low frequency**: jₗ(kr) and yₗ(kr) can be numerically unstable near k=0. Mitigation: use scipy's `spherical_jn` and `spherical_yn` with a small frequency floor.
- **Backward compatibility**: Existing tests expect a flat response. Mitigation: position callback defaults to `None`, so the mock behaves as before unless explicitly configured.
- **Performance**: FFT-based filtering adds overhead per sweep. Mitigation: the model is only used in mock mode, where performance is not critical.

# Testing

### Validation Approach
Unit tests for `SphericalHarmonicSpeakerModel` validate the mathematical correctness of the spherical harmonics and radial propagation. Integration tests verify the end-to-end flow from `MockInterfaceAudio` through the position callback.

### Key Scenarios
1. **On-axis response**: At position (r=100mm, θ=0°, z=0), the frequency response should be nearly flat for an omnidirectional model.
2. **Off-axis response**: At position (r=100mm, θ=90°, z=0), the response should show directional attenuation proportional to the dipole coefficient.
3. **Near-field effect**: At close distance (r=50mm), the response should differ from far-field (r=500mm) due to spherical wave propagation.
4. **Backward compatibility**: Without a position callback, `MockInterfaceAudio` produces the same flat response as before.
5. **Position callback integration**: `MockInterfaceAudio` with a position callback returns different IRs for different positions.

### Edge Cases
- **Zero frequency**: The radial propagation should handle f=0 gracefully (DC component).
- **Very close distance**: Near-field effects should not produce numerical overflow.
- **Default coefficients**: The model should work with only a₀₀ = 1.0 (purely omnidirectional).

### Test Changes
- New file: `tests/test_spatial_model.py` with tests for the spatial model and integration.
- Existing `test_audio_engine.py::test_integration_with_mock_audio` should still pass (no position callback by default).

# Delivery Steps

### ✓ done Step 1: Implement SphericalHarmonicSpeakerModel
Create `src/nfs/utils/spatial.py` with the `SphericalHarmonicSpeakerModel` class and helper functions for spherical harmonics and spherical wave propagation.
- Implement `real_spherical_harmonics()` for computing real SH basis functions Yₗₘ(θ, φ) up to a given order.
- Implement `spherical_bessel_hankel()` for computing radial propagation using scipy's spherical Bessel/Hankel functions.
- Implement `cylindrical_to_spherical()` to convert `CylindricalPosition` to spherical coordinates (r, θ, φ) in meters.
- Implement `SphericalHarmonicSpeakerModel.compute_frequency_response()` that combines angular SH coefficients with radial propagation to produce H(f).
- Implement `SphericalHarmonicSpeakerModel.apply_to_signal()` that applies H(f) to a time-domain signal via FFT.
- Default coefficients: a₀₀ = 1.0 (omnidirectional), a₁₀ = 0.3 (dipole along z-axis), all others zero.

### ✓ done Step 2: Integrate spatial model into MockInterfaceAudio
Modify `MockInterfaceAudio` in `src/nfs/audio.py` to accept and use the spatial speaker model.
- Add `position_callback` and `speaker_model` parameters to `MockInterfaceAudio.__init__()`.
- In `_run_sweep()`, before `apply_hardware_sim()`, query the position callback and apply the spatial model's frequency response to the sweep signal.
- Update `AudioFactory.create()` to accept optional spatial model config and pass it to `MockInterfaceAudio`.
- Add `set_position_callback()` method to `MockInterfaceAudio` for runtime configuration.

### ✓ done Step 3: Wire position callback in NearFieldScanner
Update `NearFieldScanner` in `src/nfs/nfs.py` to provide the current scanner position to the audio mock.
- Before each `capture_ir()` call, if the audio instance supports `set_position_callback()`, pass a lambda that returns `self._scanner.get_position()`.
- This ensures the mock knows the measurement position without direct coupling to the scanner class.

### ✓ done Step 4: Add tests for spatial model and integration
Create `tests/test_spatial_model.py` with comprehensive tests.
- Test `real_spherical_harmonics()` against known values (e.g., Y₀₀ = 1/√(4π), Y₁₀ ∝ cos θ).
- Test `SphericalHarmonicSpeakerModel` on-axis vs off-axis response.
- Test near-field vs far-field radial dependence.
- Test `MockInterfaceAudio` integration with position callback.
- Verify backward compatibility: existing mock audio test still passes without position callback.