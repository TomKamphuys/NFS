"""Directivity viewer window for the spatial speaker model.

Displays horizontal and vertical directivity pcolor plots generated from the
selected speaker model (spherical harmonics, discrete piston, or omnidirectional)
configured in the mock interface audio backend.
"""
from __future__ import annotations

import configparser
import math

import numpy as np

from typing import Dict, Tuple

from nfs.datatypes import CylindricalPosition

from .qt_compat import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QWidget,
)


def _build_cylindrical_positions_horizontal(distance_mm: float, n_angle: int) -> list:
    """Build cylindrical positions for a horizontal scan (z=0, varying phi)."""
    angles_deg = np.linspace(-180, 180, n_angle, endpoint=False)
    positions = []
    for angle in angles_deg:
        positions.append(CylindricalPosition(distance_mm, float(angle), 0.0))
    return positions, angles_deg


def _build_cylindrical_positions_vertical(distance_mm: float, n_elev: int) -> list:
    """Build cylindrical positions for a vertical scan (circle in x-z plane at y=0).
    
    Maps elevation angle to cylindrical coordinates:
    elev=0 → front (x=r, z=0), elev=90 → top (x=0, z=r), elev=180 → back (x=-r, z=0)
    """
    elev_angles_deg = np.linspace(-180, 180, n_elev, endpoint=False)
    positions = []
    for elev_deg in elev_angles_deg:
        elev_rad = math.radians(elev_deg)
        x_mm = distance_mm * math.cos(elev_rad)
        z_mm = distance_mm * math.sin(elev_rad)
        phi_deg = 180.0 if x_mm < 0.0 else 0.0
        positions.append(CylindricalPosition(abs(x_mm), phi_deg, z_mm))
    return positions, elev_angles_deg


def _build_directivity_plots(model) -> tuple:
    """Compute horizontal and vertical directivity magnitude arrays.

    Uses compute_frequency_response directly so the viewer shows exactly what
    the mock audio produces — no separate radial-factor code path.

    Returns (mag_h, angles_deg, mag_v, elev_angles_deg, freq_grid, n_angle, n_elev).
    """
    fs = 48000
    distance_mm = 2000.0  # 2 meters

    # Horizontal scan: z=0, varying phi from -180 to 180
    n_angle = 360
    positions_h, angles_deg = _build_cylindrical_positions_horizontal(distance_mm, n_angle)
    mag_h = np.zeros((n_angle, model._freq_bins))
    for ai, pos in enumerate(positions_h):
        H = model.compute_frequency_response(fs, pos, num_bins=model._freq_bins)
        mag_h[ai, :] = np.abs(H)

    # Normalize to front response (phi=0)
    front_idx = n_angle // 2
    front_norm = mag_h[front_idx, :]
    safe_front = np.where(front_norm > 1e-12, front_norm, 1.0)
    mag_h = mag_h / safe_front[np.newaxis, :]

    # Vertical scan: circle in x-z plane at y=0
    n_elev = 361
    positions_v, elev_angles_deg = _build_cylindrical_positions_vertical(distance_mm, n_elev)
    mag_v = np.zeros((n_elev, model._freq_bins))
    for ei, pos in enumerate(positions_v):
        H = model.compute_frequency_response(fs, pos, num_bins=model._freq_bins)
        mag_v[ei, :] = np.abs(H)

    # Normalize to front response (elev=0)
    center_idx = n_elev // 2
    center_norm = mag_v[center_idx, :]
    safe_center = np.where(center_norm > 1e-12, center_norm, 1.0)
    mag_v = mag_v / safe_center[np.newaxis, :]

    freq_grid = np.linspace(100, 20000, 200)

    return mag_h, angles_deg, mag_v, elev_angles_deg, freq_grid, n_angle, n_elev


class _DirectivityCanvas(QWidget):
    """Matplotlib canvas embedded in Qt for the two directivity plots."""

    def __init__(self, model, parent=None):
        super().__init__(parent)

        import matplotlib
        matplotlib.use("QtAgg")
        from matplotlib.backends.backend_qtagg import (
            FigureCanvasQTAgg as FigureCanvas,
        )
        from matplotlib.figure import Figure

        mag_h, angles_deg, mag_v, elev_angles_deg, freq_grid, n_angle, n_elev = (
            _build_directivity_plots(model)
        )

        # Interpolate to freq_grid
        num_bins = model._freq_bins
        mag_h_interp = np.zeros((n_angle, len(freq_grid)))
        mag_v_interp = np.zeros((n_elev, len(freq_grid)))
        for fi, freq in enumerate(freq_grid):
            bin_idx = int(freq / 24000.0 * (num_bins - 1))
            mag_h_interp[:, fi] = mag_h[:, bin_idx]
            mag_v_interp[:, fi] = mag_v[:, bin_idx]

        # Convert to dB and normalize per frequency
        mag_h_db = 20 * np.log10(np.clip(mag_h_interp, 1e-3, None))
        mag_h_db_norm = mag_h_db - mag_h_db.max(axis=0, keepdims=True)

        mag_v_db = 20 * np.log10(np.clip(mag_v_interp, 1e-3, None))
        mag_v_db_norm = mag_v_db - mag_v_db.max(axis=0, keepdims=True)

        fig = Figure(figsize=(14, 10), dpi=100)
        canvas = FigureCanvas(fig)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)

        ax_h = fig.add_subplot(2, 1, 1)
        im_h = ax_h.pcolormesh(
            freq_grid, angles_deg, mag_h_db_norm, shading="auto",
            cmap="RdBu_r", vmin=-60, vmax=0,
        )
        ax_h.set_xlabel("Frequency (Hz)", fontsize=12)
        ax_h.set_ylabel("Cylindrical θ (degrees)", fontsize=12)
        ax_h.set_title(
            "Horizontal Directivity (z=0, r=2m)\n"
            "Normalized to front response per frequency",
            fontsize=13,
        )
        ax_h.set_xlim(100, 20000)
        ax_h.set_ylim(-180, 180)
        ax_h.set_xscale("log")
        ax_h.set_yticks([-180, -135, -90, -45, 0, 45, 90, 135, 180])
        ax_h.set_yticklabels(["-180°", "-135°", "-90°", "-45°", "0°", "45°", "90°", "135°", "180°"])
        fig.colorbar(im_h, ax=ax_h, label="Magnitude (dB re max)")

        ax_v = fig.add_subplot(2, 1, 2)
        im_v = ax_v.pcolormesh(
            freq_grid, elev_angles_deg, mag_v_db_norm, shading="auto",
            cmap="RdBu_r", vmin=-60, vmax=0,
        )
        ax_v.set_xlabel("Frequency (Hz)", fontsize=12)
        ax_v.set_ylabel("Elevation angle (degrees)", fontsize=12)
        ax_v.set_title(
            "Vertical Directivity (cyl θ=0°, r=2m)\n"
            "Normalized to front response per frequency",
            fontsize=13,
        )
        ax_v.set_xlim(100, 20000)
        ax_v.set_ylim(-180, 180)
        ax_v.set_xscale("log")
        ax_v.set_yticks([-180, -135, -90, -45, 0, 45, 90, 135, 180])
        ax_v.set_yticklabels(["-180°", "-135°", "-90°", "-45°", "0°", "45°", "90°", "135°", "180°"])
        fig.colorbar(im_v, ax=ax_v, label="Magnitude (dB re max)")

        fig.tight_layout(pad=2.0)


class DirectivityViewerWindow(QDialog):
    """Standalone dialog that shows the speaker directivity plots."""

    def __init__(self, config_file: str, parent=None, spatial_model: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Speaker Directivity Viewer")
        self.resize(1200, 900)

        layout = QVBoxLayout(self)

        # Prefer an explicit selection so previews reflect unsaved settings.
        if spatial_model is None:
            parser = configparser.ConfigParser()
            parser.read(config_file)
            spatial_model = parser.get("sweep", "spatial_model", fallback="none")
        spatial_model_name = spatial_model.strip().lower()

        # Map config values to model types
        if spatial_model_name in {"spherical_harmonic", "true", "1", "yes"}:
            model_type = "spherical_harmonic"
        elif spatial_model_name == "discrete_piston":
            model_type = "discrete_piston"
        else:
            model_type = "none"

        # Build label based on selected model
        if model_type == "none":
            label_text = (
                "Directivity patterns for the virtual speaker model (omnidirectional).<br>"
                "No spatial filtering is applied — the source radiates equally in all directions."
            )
        elif model_type == "spherical_harmonic":
            label_text = (
                "Directivity patterns for the virtual speaker model (8&quot; free-field piston, spherical harmonics).<br>"
                "These patterns are used by the Mock Interface audio backend to shape the "
                "virtual measured sound at each measurement position."
            )
        else:
            label_text = (
                "Directivity patterns for the virtual speaker model (8&quot; free-field piston, discrete Rayleigh integral).<br>"
                "These patterns are used by the Mock Interface audio backend to shape the "
                "virtual measured sound at each measurement position."
            )

        label = QLabel(label_text)
        label.setWordWrap(True)
        label.setStyleSheet("color: #4b5563; padding: 6px 0;")
        layout.addWidget(label)

        # Build the appropriate directivity data based on model type
        if model_type == "none":
            mag_h, angles_deg, mag_v, elev_angles_deg = _build_omni_directivity()
        elif model_type == "spherical_harmonic":
            from nfs.utils.spatial import SphericalHarmonicSpeakerModel
            model = SphericalHarmonicSpeakerModel()
            canvas = _DirectivityCanvas(model)
            layout.addWidget(canvas)
            return
        else:
            # discrete_piston
            mag_h, angles_deg, mag_v, elev_angles_deg = _build_discrete_piston_directivity()

        # Use the generic canvas for omni and discrete piston
        freq_grid = np.linspace(100, 20000, 200)
        n_angle = len(angles_deg)
        n_elev = len(elev_angles_deg)
        canvas = _GenericDirectivityCanvas(mag_h, angles_deg, mag_v, elev_angles_deg, freq_grid, n_angle, n_elev)
        layout.addWidget(canvas)


def _build_omni_directivity() -> tuple:
    """Build flat (omnidirectional) directivity data."""
    n_angle = 360
    angles_deg = np.linspace(-180, 180, n_angle, endpoint=False)
    n_elev = 361
    elev_angles_deg = np.linspace(-180, 180, n_elev, endpoint=False)
    n_freq = 200
    mag_h = np.ones((n_angle, n_freq))
    mag_v = np.ones((n_elev, n_freq))
    return mag_h, angles_deg, mag_v, elev_angles_deg


def _build_discrete_piston_directivity() -> tuple:
    """Build directivity data for the discrete piston model by evaluating at many angles."""
    from nfs.utils.spatial import DiscretePistonSpeakerModel
    from nfs.datatypes import CylindricalPosition

    model = DiscretePistonSpeakerModel()
    distance_mm = 2000.0  # 2 meters

    # Horizontal scan: z=0, varying phi (cylindrical t)
    n_angle = 361
    angles_deg = np.linspace(-180, 180, n_angle)

    # Vertical scan: elevation angle -180 to 180
    n_elev = 361
    elev_angles_deg = np.linspace(-180, 180, n_elev)

    # Frequency grid for output
    freq_grid = np.linspace(100, 20000, 200)
    n_freq = len(freq_grid)

    # Compute horizontal directivity
    mag_h = np.zeros((n_angle, n_freq))
    for ai, phi_deg in enumerate(angles_deg):
        pos = CylindricalPosition(distance_mm, phi_deg, 0.0)
        H = model._compute_green_function_response(pos, freq_grid)
        mag_h[ai, :] = np.abs(H)

    # Normalize to front response (phi=0)
    front_idx = n_angle // 2
    front_norm = mag_h[front_idx, :]
    safe_front = np.where(front_norm > 1e-12, front_norm, 1.0)
    mag_h = mag_h / safe_front[np.newaxis, :]

    # Compute vertical directivity
    mag_v = np.zeros((n_elev, n_freq))
    for ei, elev_deg in enumerate(elev_angles_deg):
        # Map elevation to cylindrical coordinates
        # Elevation 0 = horizontal plane (z=0), elevation 90 = straight up
        alpha_rad = math.radians(elev_deg)
        x_mm = distance_mm * math.cos(alpha_rad)
        z_mm = distance_mm * math.sin(alpha_rad)
        phi_deg = 180.0 if x_mm < 0.0 else 0.0
        pos = CylindricalPosition(abs(x_mm), phi_deg, z_mm)
        H = model._compute_green_function_response(pos, freq_grid)
        mag_v[ei, :] = np.abs(H)

    # Normalize to center response (elev=0)
    center_idx = n_elev // 2
    center_norm = mag_v[center_idx, :]
    safe_center = np.where(center_norm > 1e-12, center_norm, 1.0)
    mag_v = mag_v / safe_center[np.newaxis, :]

    return mag_h, angles_deg, mag_v, elev_angles_deg


class _GenericDirectivityCanvas(QWidget):
    """Matplotlib canvas for directivity plots from pre-computed magnitude arrays."""

    def __init__(self, mag_h, angles_deg, mag_v, elev_angles_deg, freq_grid, n_angle, n_elev, parent=None):
        super().__init__(parent)

        import matplotlib
        matplotlib.use("QtAgg")
        from matplotlib.backends.backend_qtagg import (
            FigureCanvasQTAgg as FigureCanvas,
        )
        from matplotlib.figure import Figure

        # Convert to dB and normalize per frequency
        mag_h_db = 20 * np.log10(np.clip(mag_h, 1e-3, None))
        mag_h_db_norm = mag_h_db - mag_h_db.max(axis=0, keepdims=True)

        mag_v_db = 20 * np.log10(np.clip(mag_v, 1e-3, None))
        mag_v_db_norm = mag_v_db - mag_v_db.max(axis=0, keepdims=True)

        fig = Figure(figsize=(14, 10), dpi=100)
        canvas = FigureCanvas(fig)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)

        ax_h = fig.add_subplot(2, 1, 1)
        im_h = ax_h.pcolormesh(
            freq_grid, angles_deg, mag_h_db_norm, shading="auto",
            cmap="RdBu_r", vmin=-60, vmax=0,
        )
        ax_h.set_xlabel("Frequency (Hz)", fontsize=12)
        ax_h.set_ylabel("Cylindrical θ (degrees)", fontsize=12)
        ax_h.set_title(
            "Horizontal Directivity (z=0, r=2m)\n"
            "Normalized to max per frequency",
            fontsize=13,
        )
        ax_h.set_xlim(100, 20000)
        ax_h.set_ylim(-180, 180)
        ax_h.set_xscale("log")
        ax_h.set_yticks([-180, -135, -90, -45, 0, 45, 90, 135, 180])
        ax_h.set_yticklabels(["-180°", "-135°", "-90°", "-45°", "0°", "45°", "90°", "135°", "180°"])
        fig.colorbar(im_h, ax=ax_h, label="Magnitude (dB re max)")

        ax_v = fig.add_subplot(2, 1, 2)
        im_v = ax_v.pcolormesh(
            freq_grid, elev_angles_deg, mag_v_db_norm, shading="auto",
            cmap="RdBu_r", vmin=-60, vmax=0,
        )
        ax_v.set_xlabel("Frequency (Hz)", fontsize=12)
        ax_v.set_ylabel("Elevation angle (degrees)", fontsize=12)
        ax_v.set_title(
            "Vertical Directivity (cyl θ=0°, r=2m)\n"
            "Normalized to max per frequency",
            fontsize=13,
        )
        ax_v.set_xlim(100, 20000)
        ax_v.set_ylim(-180, 180)
        ax_v.set_xscale("log")
        ax_v.set_yticks([-180, -135, -90, -45, 0, 45, 90, 135, 180])
        ax_v.set_yticklabels(["-180°", "-135°", "-90°", "-45°", "0°", "45°", "90°", "135°", "180°"])
        fig.colorbar(im_v, ax=ax_v, label="Magnitude (dB re max)")

        fig.tight_layout(pad=2.0)
