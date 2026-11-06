from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import numpy as np
from scipy.special import spherical_jn, spherical_yn

from ..datatypes import CylindricalPosition

# Speed of sound in air at 20°C
SPEED_OF_SOUND = 343.0


# ───────────────────────────────────────────────
# Discrete piston (Rayleigh integral) speaker model
# ───────────────────────────────────────────────

class DiscretePistonSpeakerModel:
    """
    Simulates a virtual speaker's acoustic response using a discrete Rayleigh
    integral (cluster of monopole point sources on a flat piston disk).

    For each measurement position, the frequency response is computed by summing
    the Green's function contributions from all discrete monopole points on the
    piston surface. This is a direct physical simulation with no spherical
    harmonic approximation or truncation.

    The piston is arranged using a Fibonacci spiral (sunflower pattern) for
    even distribution of points across the disk surface.

    :param piston_radius_m: Physical radius of the simulated piston in meters.
    :param piston_point_count: Number of discrete monopole points on the piston.
    :param source_center: 3D center position of the piston (x, y, z) in meters.
    :param piston_facing: 3D normal vector indicating the direction the piston faces.
    :param volume_gain: Amplitude multiplier for the output signal.
    :param hf_taper_hz: Frequency (in Hz) where high-frequency tapering begins
                         to prevent aliasing.
    """

    def __init__(
        self,
        piston_radius_m: float = 0.1016,
        piston_point_count: int = 100,
        source_center: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        piston_facing: Tuple[float, float, float] = (1.0, 0.0, 0.0),
        volume_gain: float = 1.0,
        hf_taper_hz: float = 20000.0,
    ) -> None:
        self._piston_radius = piston_radius_m
        self._point_count = piston_point_count
        self._source_center = source_center
        self._piston_facing = piston_facing
        self._volume_gain = volume_gain
        self._hf_taper_hz = hf_taper_hz

        # Pre-generate the piston point cloud (Fibonacci spiral on disk)
        self._piston_points = _generate_piston_points(
            source_center, piston_radius_m, piston_point_count, piston_facing
        )

    def apply_to_signal(
        self,
        signal: np.ndarray,
        fs: int,
        position: CylindricalPosition,
        speaker_origin: Optional[CylindricalPosition] = None,
    ) -> np.ndarray:
        """
        Apply the discrete piston frequency response to a time-domain signal via FFT.

        :param signal: Input time-domain signal.
        :param fs: Sample rate in Hz.
        :param position: Measurement position in cylindrical coordinates.
        :param speaker_origin: Speaker origin position. Defaults to (0, 0, 0).
        :return: Filtered time-domain signal.
        """
        num_bins = len(signal) // 2 + 1
        H = self.compute_frequency_response(fs, position, speaker_origin, num_bins=num_bins)

        S = np.fft.rfft(signal)
        S_filtered = S * H
        result = np.fft.irfft(S_filtered, n=len(signal))

        return result.astype(signal.dtype)

    def compute_frequency_response(
        self,
        fs: int,
        position: CylindricalPosition,
        speaker_origin: Optional[CylindricalPosition] = None,
        num_bins: Optional[int] = None,
    ) -> np.ndarray:
        """
        Compute the complex frequency response H(f) at the given position using
        the discrete Rayleigh integral.

        The piston is unbaffled and radiates equally in front and behind.

        :param fs: Sample rate in Hz.
        :param position: Measurement position in cylindrical coordinates.
        :param speaker_origin: Speaker origin position. Defaults to (0, 0, 0).
        :param num_bins: Number of frequency bins. Defaults to fs//2 + 1.
        :return: Complex frequency response array of length num_bins.
        """
        if num_bins is None:
            num_bins = fs // 2 + 1

        freqs = np.linspace(0.0, fs / 2.0, num_bins)
        P_total = self._compute_green_function_response(
            position, freqs, speaker_origin
        )

        # High-frequency cosine taper to prevent aliasing
        if num_bins > 2 and self._hf_taper_hz > 0:
            taper = _cosine_taper_from(freqs, self._hf_taper_hz, freqs[-2])
            P_total *= taper

        # Force DC to real, Nyquist to 0
        P_total[0] = np.real(P_total[0])
        if num_bins > 1:
            P_total[-1] = 0.0

        # Apply volume gain
        P_total *= self._volume_gain

        # DC component: force to 1.0 to avoid division by zero in deconvolution
        P_total[0] = 1.0 + 0j

        return P_total

    def _compute_green_function_response(
        self,
        position: CylindricalPosition,
        frequencies: np.ndarray,
        speaker_origin: Optional[CylindricalPosition] = None,
    ) -> np.ndarray:
        """Sum the piston point-source responses at the requested frequencies."""
        mic_pos = _cylindrical_to_cartesian(position, speaker_origin)
        dists = np.maximum(
            np.linalg.norm(self._piston_points - mic_pos, axis=1), 1e-6
        )
        frequencies = np.asarray(frequencies, dtype=float).reshape(-1)
        response = np.empty(frequencies.size, dtype=np.complex128)
        point_denominator = 4.0 * np.pi * dists

        # Bound temporary memory for the full audio FFT while keeping preview
        # requests vectorized over their much smaller frequency grid.
        chunk_size = 1024
        for start in range(0, frequencies.size, chunk_size):
            stop = min(start + chunk_size, frequencies.size)
            wave_numbers = 2.0 * np.pi * frequencies[start:stop] / SPEED_OF_SOUND
            green_functions = np.exp(
                -1j * wave_numbers[:, np.newaxis] * dists[np.newaxis, :]
            ) / point_denominator[np.newaxis, :]
            response[start:stop] = np.mean(green_functions, axis=1)

        return response


def _generate_piston_points(
    center: Tuple[float, float, float],
    radius: float,
    count: int,
    normal: Tuple[float, float, float],
) -> np.ndarray:
    """
    Generate a cluster of 3D points arranged in a flat disk using a Fibonacci
    spiral (sunflower pattern) for even distribution, then rotate to face the
    given normal direction.
    """
    indices = np.arange(0, count, dtype=float) + 0.5
    r = np.sqrt(indices / count)
    theta = np.pi * (1 + 5**0.5) * indices

    points_local = np.column_stack((
        r * np.cos(theta) * radius,
        r * np.sin(theta) * radius,
        np.zeros(count),
    ))

    normal = np.array(normal) / np.linalg.norm(normal)
    z_axis = np.array([0, 0, 1])

    if np.allclose(normal, z_axis):
        R = np.eye(3)
    elif np.allclose(normal, -z_axis):
        R = np.diag([1, -1, -1])
    else:
        v = np.cross(z_axis, normal)
        s = np.linalg.norm(v)
        c = np.dot(z_axis, normal)
        vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
        R = np.eye(3) + vx + (vx @ vx) * ((1 - c) / (s**2))

    return (points_local @ R.T) + np.array(center)


def _cylindrical_to_cartesian(
    pos: CylindricalPosition,
    origin: Optional[CylindricalPosition] = None,
) -> np.ndarray:
    """Convert CylindricalPosition to 3D Cartesian (x, y, z) in meters."""
    if origin is None:
        origin = CylindricalPosition(0.0, 0.0, 0.0)

    r_xy_mm = pos.r() - origin.r()
    phi_deg = pos.t() - origin.t()
    z_mm = pos.z() - origin.z()

    r_xy_m = r_xy_mm / 1000.0
    z_m = z_mm / 1000.0
    phi_rad = math.radians(phi_deg)

    return np.array([r_xy_m * math.cos(phi_rad), r_xy_m * math.sin(phi_rad), z_m])


def _cosine_taper_from(freqs_hz: np.ndarray, f_start: float, f_end: float) -> np.ndarray:
    """
    Create a cosine-shaped window that tapers from 1.0 down to 0.0
    between f_start and f_end, and is 0.0 above f_end.
    """
    w = np.ones_like(freqs_hz)
    if f_end <= f_start:
        return w

    m = (freqs_hz >= f_start) & (freqs_hz <= f_end)
    x = (freqs_hz[m] - f_start) / (f_end - f_start)
    w[m] = 0.5 * (1.0 + np.cos(np.pi * x))
    w[freqs_hz > f_end] = 0.0
    return w


def cylindrical_to_spherical(
    pos: CylindricalPosition,
    origin: Optional[CylindricalPosition] = None,
) -> Tuple[float, float, float]:
    """
    Convert a CylindricalPosition to spherical coordinates (r, theta, phi) in meters.

    The cylindrical system uses (r_xy_mm, phi_deg, z_mm). The spherical system
    returns (r_m, theta_rad, phi_rad) where theta is the polar angle measured
    from the +x axis (the speaker firing direction) and phi is the azimuthal angle
    in the y-z plane around the +x axis. This alignment ensures that an axisymmetric
    directivity pattern about +x uses only m=0 spherical harmonic coefficients.

    :param pos: Measurement position in cylindrical coordinates (mm, deg, mm).
    :param origin: Speaker origin in cylindrical coordinates. Defaults to (0, 0, 0).
    :return: Tuple (r_m, theta_rad, phi_rad).
    """
    if origin is None:
        origin = CylindricalPosition(0.0, 0.0, 0.0)

    # Convert to Cartesian (mm)
    r_xy = pos.r() - origin.r()
    phi_deg = pos.t() - origin.t()
    z = pos.z() - origin.z()

    phi_rad = math.radians(phi_deg)
    x = r_xy * math.cos(phi_rad)
    y = r_xy * math.sin(phi_rad)

    # Spherical coordinates with polar axis along +x (speaker firing direction)
    r_m = math.sqrt(x ** 2 + y ** 2 + z ** 2) / 1000.0  # mm -> m
    if r_m < 1e-12:
        # At the origin, angles are undefined; return defaults
        return 0.0, 0.0, 0.0

    # Theta: polar angle from +x axis
    theta_rad = math.acos(max(-1.0, min(1.0, x / (r_m * 1000.0))))
    # Phi: azimuthal angle in the y-z plane around +x
    phi_rad = math.atan2(z, y)
    # Normalize to [0, 2π)
    phi_rad = phi_rad % (2.0 * math.pi)

    return r_m, theta_rad, phi_rad


def real_spherical_harmonics(
    order: int, theta: float, phi: float
) -> Dict[Tuple[int, int], float]:
    """
    Compute real spherical harmonic basis functions Yₗₘ(θ, φ) up to the given order.

    Uses the real (4π-normalized) convention:
      Y₀₀ = 1 / √(4π)
      Y₁₀ ∝ cos θ,  Y₁₁ ∝ sin θ cos φ,  Y₁₋₁ ∝ sin θ sin φ

    :param order: Maximum degree l (e.g., 2 means l=0,1,2).
    :param theta: Polar angle in radians (0 = +x axis, speaker firing direction).
    :param phi: Azimuthal angle in radians (in the y-z plane).
    :return: Dictionary mapping (l, m) -> Yₗₘ(θ, φ).
    """
    result: Dict[Tuple[int, int], float] = {}
    st = math.sin(theta)
    ct = math.cos(theta)
    sp = math.sin(phi)
    cp = math.cos(phi)

    for l in range(order + 1):
        for m in range(-l, l + 1):
            result[(l, m)] = _real_sh_lm(l, m, st, ct, sp, cp)

    return result


def _real_sh_lm(l: int, m: int, st: float, ct: float, sp: float, cp: float) -> float:
    """
    Evaluate a single real spherical harmonic Yₗₘ using the 4π-normalized convention.

    The computation uses associated Legendre functions via recursive evaluation.
    """
    if m == 0:
        return _sh_normalized(l, 0) * _associated_legendre(l, 0, ct)
    elif m > 0:
        return _sh_normalized(l, m) * _associated_legendre(l, m, ct) * math.sqrt(2.0) * math.cos(m * math.atan2(sp, cp))
    else:
        # m < 0
        abs_m = -m
        return _sh_normalized(l, abs_m) * _associated_legendre(l, abs_m, ct) * math.sqrt(2.0) * math.sin(abs_m * math.atan2(sp, cp))


def _sh_normalized(l: int, m: int) -> float:
    """Compute the normalization factor for real spherical harmonics (4π-normalized)."""
    from math import factorial, pi

    if m == 0:
        return math.sqrt((2.0 * l + 1.0) / (4.0 * pi))
    else:
        return math.sqrt((2.0 * l + 1.0) / (4.0 * pi) * factorial(l - abs(m)) / factorial(l + abs(m)))


def _associated_legendre(l: int, m: int, x: float) -> float:
    """
    Compute the associated Legendre function Pₗₘ(x) using recursion.

    :param l: Degree.
    :param m: Order (non-negative).
    :param x: cos(theta), in [-1, 1].
    :return: Pₗₘ(x).
    """
    if m < 0:
        raise ValueError("m must be non-negative")
    if m > l:
        return 0.0

    # Start with P₀₀ = 1
    if l == 0:
        return 1.0

    # Compute Pₘₘ first: Pₘₘ(x) = (-1)^m * (2m-1)!! * (1-x^2)^(m/2)
    # Using the fully normalized convention
    pm_m = 1.0
    if m > 0:
        fact = 1.0
        for i in range(1, 2 * m, 2):
            fact *= i
        pm_m = fact * ((1.0 - x * x) ** (m / 2.0))

    if l == m:
        return pm_m

    # Pₘ₊₁,ₘ
    pmm1_m = x * (2.0 * m + 1.0) * pm_m

    if l == m + 1:
        return pmm1_m

    # Recurse up in l using the standard three-term recurrence
    for ll in range(m + 2, l + 1):
        p_new = (x * (2.0 * ll - 1.0) * pmm1_m - (ll + m - 1.0) * pm_m) / (ll - m)
        pm_m = pmm1_m
        pmm1_m = p_new

    return pmm1_m


def spherical_bessel_hankel(
    l: int, kr: np.ndarray
) -> np.ndarray:
    """
    Compute the spherical Hankel function of the second kind hₗ(kr) = jₗ(kr) - i·yₗ(kr).

    This represents outgoing spherical waves for radial propagation under the
    engineering/audio FFT time convention (e^(+jωt)).

    :param l: Order of the spherical Bessel/Hankel function.
    :param kr: Wavenumber × distance array (k = 2πf/c, r = distance in meters).
    :return: Complex array of hₗ(kr).
    """
    # Handle kr ≈ 0 to avoid division by zero in spherical_yn
    j = spherical_jn(l, kr)
    y = spherical_yn(l, np.where(kr < 1e-12, 1e-12, kr))
    return j - 1j * y


def _compute_piston_sh_coeffs(
    order: int, ka: np.ndarray, freq_bins: int
) -> Dict[Tuple[int, int], np.ndarray]:
    """
    Compute spherical harmonic coefficients for a free-field circular piston.

    The free-field piston directivity combines the baffled piston pattern with
    a cosine factor to model front-only radiation:
      D(α) = [2·J₁(ka·sinα) / (ka·sinα)] · (1 + cos(α)) / 2
    where α is the angle from the piston's axis (+x direction).

    The spherical harmonic basis is aligned so that the polar axis (+z in SH space)
    coincides with the speaker's firing direction (+x in physical space). This means
    the axisymmetric directivity uses only m=0 coefficients, minimizing the required
    SH order.

    The factor (1 + cos(α)) / 2 ensures:
      - D = 1 at α = 0° (front of speaker)
      - D = 0 at α = 180° (back of speaker)
      - D = 0.5 at α = 90° (sides)

    This models a speaker in a sealed cabinet that blocks rear radiation.

    :param order: Maximum SH degree.
    :param ka: Array of ka values (wavenumber × piston radius) for each frequency.
    :param freq_bins: Number of frequency bins.
    :return: Dictionary mapping (l, m) -> coefficient array of length freq_bins.
    """
    from scipy.special import j1

    # Use Gauss-Legendre quadrature for numerical integration over the sphere
    # For order N, we need at least 2N+1 points for exact integration of polynomials
    # Using 2*order+5 gives good accuracy with minimal computation
    n_quad = 2 * order + 5
    mu, weights_mu = np.polynomial.legendre.leggauss(n_quad)  # cos(theta) samples
    # phi is uniform, use trapezoidal rule
    n_phi = 4 * order + 8
    phi_pts = np.linspace(0, 2.0 * np.pi, n_phi, endpoint=False)
    weights_phi = np.ones(n_phi) * (2.0 * np.pi / n_phi)

    # Build grid of (theta, phi) -> (mu, phi)
    # mu = cos(theta), theta in [0, pi]
    # With the polar axis aligned to +x (speaker firing direction),
    # theta is the angle from +x, so cos(alpha) = cos(theta) = mu directly.
    mu_grid, phi_grid = np.meshgrid(mu, phi_pts, indexing="ij")
    cos_alpha = mu_grid  # cos(angle from +x axis) = cos(theta)

    # Precompute real SH values at all quadrature points (vectorized)
    # sh_values[(l, m)] = Yₗₘ(theta, phi) for each quadrature point
    theta_grid = np.arccos(np.clip(mu_grid, -1.0, 1.0))
    st_grid = np.sin(theta_grid)
    ct_grid = np.cos(theta_grid)
    sp_grid = np.sin(phi_grid)
    cp_grid = np.cos(phi_grid)

    sh_values: Dict[Tuple[int, int], np.ndarray] = {}
    for l in range(order + 1):
        for m in range(-l, l + 1):
            sh_values[(l, m)] = _real_sh_lm_vectorized(
                l, m, st_grid, ct_grid, sp_grid, cp_grid
            )

    # Compute the SH coefficients for each frequency bin
    # aₗₘ = ∫∫ D(α) · Yₗₘ(θ, φ) sinθ dθ dφ / ∫∫ Yₗₘ² sinθ dθ dφ
    # With Gauss-Legendre: ∫ f(θ) sinθ dθ = ∫ f(mu) d(mu) ≈ Σ w_i · f(mu_i)
    # The normalization of real SH ensures ∫∫ Yₗₘ² dΩ = 1

    # sinα = sqrt(1 - cos²α)
    sin_alpha = np.sqrt(1.0 - cos_alpha ** 2)

    # The cosine factor for front-only radiation: (1 + cos(α)) / 2
    # This is 1 at front (α=0), 0 at back (α=180°), 0.5 at sides (α=90°)
    # However, at low frequencies the wavelength is much larger than the cabinet,
    # so the source should be nearly omnidirectional. We blend between
    # omnidirectional (1.0) and the cosine factor based on ka.
    # The transition happens around ka ≈ 1 (frequency where wavelength ~ piston diameter).
    cosine_raw = (1.0 + cos_alpha) / 2.0

    # Initialize coefficient arrays
    coeffs: Dict[Tuple[int, int], np.ndarray] = {}

    # Process in chunks to avoid memory issues
    chunk_size = 1000
    for start in range(0, freq_bins, chunk_size):
        end = min(start + chunk_size, freq_bins)
        chunk_len = end - start
        ka_chunk = ka[start:end]  # shape: (chunk_len,)

        # Compute directivity for all frequencies and all quadrature points
        x = ka_chunk[:, None, None] * sin_alpha[None, :, :]  # (chunk_len, n_quad, n_phi)

        # Baffled piston: D_baffled(α) = 2·J₁(x) / x, with D(0) = 1
        # Handle x ≈ 0: J₁(x)/x → 1/2, so D → 1
        D_baffled = np.ones((chunk_len, n_quad, n_phi))
        nonzero = x > 1e-12
        D_baffled[nonzero] = 2.0 * j1(x[nonzero]) / x[nonzero]

        # Frequency-dependent cosine factor: at low ka the source is omnidirectional,
        # at high ka the cabinet blocks rear radiation. Blend using a smooth
        # sigmoid-like transition centered at ka ≈ 1.
        # weight = ka² / (ka² + 1) goes from 0 (omni) at ka=0 to 1 (full cosine) at ka>>1
        weight = ka_chunk[:, None, None] ** 2 / (ka_chunk[:, None, None] ** 2 + 1.0)
        cosine_factor = 1.0 - weight + weight * cosine_raw[None, :, :]

        # Free-field piston: multiply by cosine factor for front-only radiation
        D = D_baffled * cosine_factor

        # Project onto each SH basis function
        # aₗₘ[f] = Σ_i Σ_j D[f, i, j] · Yₗₘ[i, j] · w_mu[i] · w_phi[j]
        for (l, m), y_vals in sh_values.items():
            # y_vals: (n_quad, n_phi)
            # weights: (n_quad, n_phi) = w_mu[:, None] * w_phi[None, :]
            weighted_sh = y_vals * (weights_mu[:, None] * weights_phi[None, :])
            # aₗₘ[f] = Σ_i,j D[f,i,j] * weighted_sh[i,j]
            # Reshape D to (chunk_len, n_quad*n_phi) for matrix multiply
            D_flat = D.reshape(chunk_len, -1)
            a_lm = D_flat @ weighted_sh.ravel()

            if (l, m) not in coeffs:
                coeffs[(l, m)] = np.zeros(freq_bins, dtype=np.float64)
            coeffs[(l, m)][start:end] = a_lm

    return coeffs


def _real_sh_lm_scalar(l: int, m: int, theta: float, phi: float) -> float:
    """Evaluate a single real spherical harmonic Yₗₘ(θ, φ) at scalar angles."""
    st = math.sin(theta)
    ct = math.cos(theta)
    sp = math.sin(phi)
    cp = math.cos(phi)
    return _real_sh_lm(l, m, st, ct, sp, cp)


def _real_sh_lm_vectorized(
    l: int, m: int, st: np.ndarray, ct: np.ndarray, sp: np.ndarray, cp: np.ndarray
) -> np.ndarray:
    """
    Vectorized version of _real_sh_lm for numpy arrays.
    Uses numpy operations instead of scalar math functions.
    """
    from math import factorial, pi

    if m == 0:
        return _sh_normalized(l, 0) * _associated_legendre_vectorized(l, 0, ct)
    elif m > 0:
        norm = _sh_normalized(l, m)
        plm = _associated_legendre_vectorized(l, m, ct)
        return norm * plm * np.sqrt(2.0) * np.cos(m * np.arctan2(sp, cp))
    else:
        # m < 0
        abs_m = -m
        norm = _sh_normalized(l, abs_m)
        plm = _associated_legendre_vectorized(l, abs_m, ct)
        return norm * plm * np.sqrt(2.0) * np.sin(abs_m * np.arctan2(sp, cp))


def _associated_legendre_vectorized(
    l: int, m: int, x: np.ndarray
) -> np.ndarray:
    """
    Vectorized associated Legendre function Pₗₘ(x) using recursion.
    Works element-wise on numpy arrays.
    """
    if m < 0:
        raise ValueError("m must be non-negative")
    if m > l:
        return np.zeros_like(x)

    if l == 0:
        return np.ones_like(x, dtype=np.float64)

    # Compute Pₘₘ: Pₘₘ(x) = (2m-1)!! * (1-x^2)^(m/2)
    pm_m = np.ones_like(x, dtype=np.float64)
    if m > 0:
        fact = 1.0
        for i in range(1, 2 * m, 2):
            fact *= i
        pm_m = fact * ((1.0 - x * x) ** (m / 2.0))

    if l == m:
        return pm_m

    # Pₘ₊₁,ₘ
    pmm1_m = x * (2.0 * m + 1.0) * pm_m

    if l == m + 1:
        return pmm1_m

    # Recurse up in l
    for ll in range(m + 2, l + 1):
        p_new = (x * (2.0 * ll - 1.0) * pmm1_m - (ll + m - 1.0) * pm_m) / (ll - m)
        pm_m = pmm1_m
        pmm1_m = p_new

    return pmm1_m


class SphericalHarmonicSpeakerModel:
    """
    Simulates a virtual speaker's acoustic response using spherical harmonics
    for angular directivity and an exponential Green's function for radial
    distance dependence.

    The frequency response H(f, r, θ, φ) is computed as:
      H(f) = [Σₗₘ aₗₘ(f) · Yₗₘ(θ, φ)] · exp(-j·k·(r - r₀)) · (r₀ / r)

    where the angular part uses real spherical harmonics and the radial part
    uses the outgoing-wave Green's function from the engineering/audio FFT
    convention (e^(+jωt)), matching the discrete piston model's behavior.
    """

    def __init__(
        self,
        order: int = 8,
        coeffs: Optional[Dict[Tuple[int, int], np.ndarray]] = None,
        freq_bins: Optional[int] = None,
    ) -> None:
        """
        Initialize the speaker model.

        :param order: Maximum spherical harmonic degree (default 8).
        :param coeffs: Frequency-dependent coefficients aₗₘ(f). Keys are (l, m)
                       tuples, values are 1-D arrays of length freq_bins.
                       If None, uses default simple model.
        :param freq_bins: Number of frequency bins for coefficients.
                          Required if coeffs is provided.
        """
        self._order = order

        if coeffs is not None and freq_bins is not None:
            self._coeffs = coeffs
            self._freq_bins = freq_bins
        else:
            # Default simple model: omnidirectional + dipole
            self._coeffs, self._freq_bins = self._default_coeffs(order)

    @staticmethod
    def _default_coeffs(order: int, freq_bins: Optional[int] = None) -> Tuple[Dict[Tuple[int, int], np.ndarray], int]:
        """
        Generate default spherical harmonic coefficients for an 8" free-field piston speaker.

        The speaker fires along the radial axis (cylindrical theta=0, z=0), which
        corresponds to the +x direction in Cartesian coordinates.

        Uses a free-field circular piston model: the far-field directivity combines
        the baffled piston pattern D_baffled(α) = 2·J₁(ka·sinα) / (ka·sinα) with
        a cosine factor (1 + cos(α)) / 2 to model front-only radiation. This ensures
        the response is unity at the front (α=0°), near zero at the back (α=180°),
        and attenuated at the sides (α=90°).

        For an 8" driver (diameter 203.2 mm, radius 101.6 mm), the directivity
        becomes significant above ~500 Hz and narrows with increasing frequency,
        matching real woofer behavior in a sealed cabinet.

        Spherical harmonic coefficients are obtained by numerically projecting
        the directivity pattern onto the real SH basis at each frequency.
        """
        if freq_bins is None:
            freq_bins = 24577  # Default for 48kHz sample rate

        # Frequency array (assume 48 kHz sample rate for coefficient generation)
        fs = 48000
        frequencies = np.linspace(0, fs / 2.0, freq_bins)

        # 8" driver: diameter 203.2 mm, radius 101.6 mm
        piston_radius_m = 0.1016

        # Wavenumber × radius for each frequency: ka = 2πf·a/c
        ka = 2.0 * np.pi * frequencies * piston_radius_m / SPEED_OF_SOUND

        # Compute SH coefficients by projecting the piston directivity onto the
        # real SH basis. The piston fires along +x, so the angle α from the
        # piston axis for a point with spherical coordinates (θ, φ) is:
        #   cos(α) = sin(θ)·cos(φ)
        # The directivity D(α) is axisymmetric about +x.
        # We project D(α) onto real SH basis using numerical integration.

        coeffs = _compute_piston_sh_coeffs(order, ka, freq_bins)

        return coeffs, freq_bins

    def compute_frequency_response(
        self,
        fs: int,
        position: CylindricalPosition,
        speaker_origin: Optional[CylindricalPosition] = None,
        num_bins: Optional[int] = None,
    ) -> np.ndarray:
        """
        Compute the complex frequency response H(f) at the given position using
        spherical harmonics for angular directivity and exponential Green's function
        for radial propagation.

        The angular directivity is represented by spherical harmonic coefficients
        aₗₘ projected onto real spherical harmonics Yₗₘ(θ, φ). The radial
        propagation uses the outgoing-wave Green's function exp(-jkr) from the
        engineering/audio FFT convention (e^(+jωt)), matching the discrete piston
        model's phase behavior.

        The frequency response is:
          H(f) = [Σ aₗₘ(f) · Yₗₘ(θ, φ)] · exp(-jkr) / r

        where exp(-jkr) is the outgoing-wave Green's function phase and 1/r is the
        spherical wave amplitude decay.

        :param fs: Sample rate in Hz.
        :param position: Measurement position in cylindrical coordinates.
        :param speaker_origin: Speaker origin position. Defaults to (0, 0, 0).
        :param num_bins: Number of frequency bins. Defaults to fs//2 + 1.
        :return: Complex frequency response array of length num_bins.
        """
        if num_bins is None:
            num_bins = fs // 2 + 1

        r_m, theta, phi = cylindrical_to_spherical(position, speaker_origin)

        # Wavenumber array
        k = 2.0 * np.pi * np.linspace(0, fs / 2.0, num_bins) / SPEED_OF_SOUND

        # Evaluate real spherical harmonics at the measurement angle (scalar per l,m)
        sh_vals = real_spherical_harmonics(self._order, theta, phi)

        # Radial propagation: outgoing-wave Green's function exp(-jkr) with 1/r decay.
        # This matches the discrete piston model's convention and produces
        # a clean impulse response with correct time delay.
        kr = k * r_m
        radial = np.exp(-1j * kr) / (r_m + 1e-12)

        # Collapse (l, m) pairs: for each l, sum aₗₘ · Yₗₘ over all m
        # H = [Σ_l Σ_m aₗₘ · Yₗₘ] · radial
        H = np.zeros(num_bins, dtype=np.complex128)
        angular_sum = np.zeros(num_bins, dtype=np.complex128)
        for l in range(self._order + 1):
            for m in range(-l, l + 1):
                key = (l, m)
                if key not in self._coeffs:
                    continue
                a_interp = self._interpolate_coeffs(
                    self._coeffs[key], self._freq_bins, num_bins
                )
                y_lm = sh_vals.get(key, 0.0)
                if y_lm != 0.0:
                    angular_sum += a_interp * y_lm
        H = angular_sum * radial

        # DC component: force to 1.0 to avoid division by zero in deconvolution
        H[0] = 1.0 + 0j

        return H

    def apply_to_signal(
        self,
        signal: np.ndarray,
        fs: int,
        position: CylindricalPosition,
        speaker_origin: Optional[CylindricalPosition] = None,
    ) -> np.ndarray:
        """
        Apply the spatial frequency response to a time-domain signal via FFT.

        :param signal: Input time-domain signal.
        :param fs: Sample rate in Hz.
        :param position: Measurement position in cylindrical coordinates.
        :param speaker_origin: Speaker origin position. Defaults to (0, 0, 0).
        :return: Filtered time-domain signal.
        """
        # Compute frequency response with the correct number of bins for the signal length
        num_bins = len(signal) // 2 + 1
        H = self.compute_frequency_response(fs, position, speaker_origin, num_bins=num_bins)

        # FFT of the signal
        S = np.fft.rfft(signal)

        # Apply frequency response
        S_filtered = S * H

        # Inverse FFT back to time domain
        result = np.fft.irfft(S_filtered, n=len(signal))

        return result.astype(signal.dtype)

    def _interpolate_coeffs(
        self, coeffs: np.ndarray, orig_bins: int, target_bins: int
    ) -> np.ndarray:
        """
        Interpolate frequency-dependent coefficients to match target frequency bins.

        :param coeffs: Original coefficient array.
        :param orig_bins: Number of bins in the original array.
        :param target_bins: Desired number of bins.
        :return: Interpolated coefficient array.
        """
        if orig_bins == target_bins:
            return coeffs

        # Linear interpolation in frequency domain
        orig_x = np.linspace(0, 1, orig_bins)
        target_x = np.linspace(0, 1, target_bins)
        return np.interp(target_x, orig_x, coeffs)
