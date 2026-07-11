#!/usr/bin/env python3
"""
PyVista / PySide6 Interactive 3D Replay Viewer Engine
Real-sphere-glyph version
-----------------------------------------------------

Embeddable PySide6 widget for a Klippel-style cylindrical measurement path.

This version deliberately uses real sphere glyph geometry for the static
measurement cloud, because this proved robust in QtInteractor on Windows,
where point sprites / gaussian points were unreliable.

Expected input columns:
    phi_deg
    r_xy_mm
    z_mm

Optional column:
    gen_settings
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from matplotlib.colors import LinearSegmentedColormap
from loguru import logger
import numpy as np
import pandas as pd
import pyvista as pv
import vtk
from pyvistaqt import QtInteractor
from PySide6 import QtCore, QtGui, QtWidgets


PYVISTA_VIEWER_BUILD = "real-sphere-glyph-cloud-split-grid-bounds-z0-datum-2026-06-29"
ROBOT_MODEL_UNITS_TO_METERS = 0.01  # Fusion OBJ exports are unitless; these files appear to be centimetre-scaled.
DARK_BG_ELEVATION_CMAP = LinearSegmentedColormap.from_list(
    "dark_bg_elevation",
    ["#38bdf8", "#34d399", "#facc15", "#fb7185"],
    N=256,
)

# Keep VTK's occasional camera singularity warnings out of normal app output.
# Real VTK errors still print; raise this to VERBOSITY_WARNING while debugging VTK.
vtk.vtkLogger.SetStderrVerbosity(vtk.vtkLogger.VERBOSITY_ERROR)

DataLike = Union[pd.DataFrame, dict, str]


def _hsv_slider_to_rgb01(value: float) -> tuple[float, float, float]:
    value = max(0.0, min(1.0, float(value)))
    if value <= 0.0:
        return (0.0, 0.0, 0.0)
    return colorsys.hsv_to_rgb(value % 1.0, 1.0, 1.0)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str) and value.strip().lower() in {"", "none", "nan"}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _settings_from_df(df: pd.DataFrame) -> dict[str, str]:
    """Extract key=value strings from optional gen_settings column.

    Supports:
      - one setting per row: cyl_radius_internal=0.2
      - multiple settings in one cell separated by comma/semicolon/newline
      - dict-like values, if the DataFrame was constructed programmatically
    """
    settings: dict[str, str] = {}

    if "gen_settings" not in df.columns:
        return settings

    for item in df["gen_settings"].dropna().tolist():
        if isinstance(item, dict):
            for key, value in item.items():
                settings[str(key).strip()] = str(value).strip()
            continue

        text = str(item)
        normalized = text.replace(";", "\n").replace(",", "\n")
        for chunk in normalized.splitlines():
            chunk = chunk.strip()
            if "=" not in chunk:
                continue
            key, value = chunk.split("=", 1)
            settings[key.strip()] = value.strip()

    return settings


def _polyline_from_points(points: np.ndarray) -> pv.PolyData:
    """Create a single connected VTK polyline from an N*3 points array.

    For fewer than two points, returns a tiny zero-length line at origin so VTK
    does not complain about empty geometry.
    """
    points = np.asarray(points, dtype=float)

    if len(points) == 0:
        points = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]], dtype=float)
    elif len(points) == 1:
        points = np.vstack([points, points])

    mesh = pv.PolyData(points)
    n = len(points)
    mesh.lines = np.concatenate(([n], np.arange(n, dtype=np.int64)))
    return mesh


def _line_segments_from_points(points: np.ndarray, segments: list[tuple[int, int]]) -> pv.PolyData:
    """Create disconnected line segments from a point array and index pairs."""
    mesh = pv.PolyData(np.asarray(points, dtype=float))
    if not segments:
        mesh.lines = np.empty(0, dtype=np.int64)
        return mesh
    mesh.lines = np.asarray(
        [[2, int(start), int(end)] for start, end in segments],
        dtype=np.int64,
    ).ravel()
    return mesh


def _colour_scalars_from_z(points: np.ndarray) -> np.ndarray:
    z = points[:, 2]
    span = z.max() - z.min()
    if span == 0:
        return np.zeros_like(z)
    return (z - z.min()) / span


def _make_sphere_glyphs(points: np.ndarray, radius_m: float) -> pv.PolyData:
    """Turn each point into a small real sphere mesh."""
    points = np.asarray(points, dtype=float)

    cloud = pv.PolyData(points)
    cloud["z_norm"] = _colour_scalars_from_z(points)

    sphere = pv.Sphere(
        radius=radius_m,
        theta_resolution=10,
        phi_resolution=10,
    )

    glyphs = cloud.glyph(
        geom=sphere,
        scale=False,
        orient=False,
    )

    # The glyph filter preserves the active scalars, but be defensive.
    if "z_norm" not in glyphs.array_names:
        # Repeat point scalars approximately by nearest original point count.
        # This fallback is rarely needed.
        glyphs["z_norm"] = np.linspace(0.0, 1.0, glyphs.n_points)

    return glyphs


def _make_plain_sphere_glyphs(points: np.ndarray, radius_m: float) -> pv.PolyData:
    points = np.asarray(points, dtype=float)
    if len(points) == 0:
        return pv.PolyData()

    cloud = pv.PolyData(points)
    sphere = pv.Sphere(
        radius=radius_m,
        theta_resolution=8,
        phi_resolution=8,
    )
    return cloud.glyph(
        geom=sphere,
        scale=False,
        orient=False,
    )


def _sphere_at(center: tuple[float, float, float], radius_m: float) -> pv.PolyData:
    return pv.Sphere(
        radius=radius_m,
        center=center,
        theta_resolution=16,
        phi_resolution=16,
    )


@dataclass
class BoundsInfo:
    has_inner: bool = False
    has_outer: bool = False
    r_int: float = 0.0
    h_int: float = 0.0
    r_ext: float = 0.0
    h_ext: float = 0.0
    z_center: float = 0.0


class CoordViewerPyVista(QtWidgets.QWidget):
    """
    PySide6 QWidget containing a PyVista/VTK 3D viewport.

        viewer = CoordViewerPyVista()
        layout.addWidget(viewer)
        viewer.load_data(df)
    """

    def __init__(
        self,
        input_data: Optional[DataLike] = None,
        parent=None,
        cloud_sphere_radius_mm: float = 8.0,
        head_sphere_radius_mm: float = 14.0,
    ):
        super().__init__(parent)

        logger.debug("CoordViewerPyVista build: {}", PYVISTA_VIEWER_BUILD)

        # Playback state
        self.curr_idx = 0
        self.exact_idx = 0.0
        self.is_playing = False
        self.ppm = 600.0
        self.tail_length = 50
        self.use_history_fading = False
        self.use_ortho = False
        self.timer_interval_ms = 50
        self.show_readout = True
        self._is_shutting_down = False

        # Visual state
        self.use_white_background = False
        self.show_bounds = True
        self.show_grid = False
        self.current_alpha = 1.0
        self.current_color_val = 0.5
        self.point_color_mode = "elevation"
        self.bounds = BoundsInfo()
        self.cloud_sphere_radius_m = float(cloud_sphere_radius_mm) / 1000.0
        self.head_sphere_radius_m = float(head_sphere_radius_mm) / 1000.0

        # Rotation state
        self.is_rotating = True
        self.rot_full_angle = 45.0
        self.rot_target_angle = 22.5
        self.rot_dir = 1
        self.rot_accumulated = 0.0
        self.rotation_speed_deg_per_sec = 5.0

        # Data arrays
        self.N = 0
        self.phi_arr = np.array([], dtype=float)
        self.r_m = np.array([], dtype=float)
        self.z_m = np.array([], dtype=float)
        self.x = np.array([], dtype=float)
        self.y = np.array([], dtype=float)
        self.z = np.array([], dtype=float)
        self._base_points = np.empty((0, 3), dtype=float)
        self._camera_bounds: Optional[tuple[float, float, float, float, float, float]] = None

        # Qt layout
        self.plotter = QtInteractor(self)
        self._set_plot_background()
        self.plotter.add_axes()
        try:
            self.plotter.enable_anti_aliasing("msaa")
        except Exception:
            pass
        try:
            # Terrain style keeps Z as the natural up axis, so roll is constrained
            # by the native interactor instead of corrected by jittery camera hooks.
            self.plotter.enable_terrain_style(mouse_wheel_zooms=True)
        except Exception:
            pass
        self.plotter.camera.up = (0.0, 0.0, 1.0)
        self._install_camera_up_stabilizer()

        self.readout_label = QtWidgets.QLabel("")
        self.readout_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.readout_label.setStyleSheet(
            "QLabel {"
            "font-family: monospace;"
            "font-size: 10pt;"
            "background: rgba(255, 255, 255, 210);"
            "border: 1px solid #cccccc;"
            "border-radius: 4px;"
            "padding: 6px;"
            "}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._install_viewport_chrome()
        self.plotter.interactor.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self.readout_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.setMinimumHeight(700)
        layout.addWidget(self.plotter.interactor, stretch=1)
        layout.addWidget(self.readout_label, stretch=0)

        # Actors
        self.base_actor = None
        self.hist_actor = None
        self.active_actor = None
        self.head_actor = None
        self.inner_actor = None
        self.inner_outline_actor = None
        self.outer_actor = None
        self.outer_outline_actor = None
        self.outer_cap_rim_actor = None
        self.front_back_marker_actor = None
        self.front_back_label_actor = None
        self.bounds_box_actor = None
        self.robot_phi_actor = None
        self.robot_r_actor = None
        self.robot_z_actor = None
        self.robot_model_loaded = False
        self._hist_mesh = _polyline_from_points(np.empty((0, 3)))
        self._active_mesh = _polyline_from_points(np.empty((0, 3)))
        self._head_mesh = _sphere_at((0.0, 0.0, 0.0), self.head_sphere_radius_m)

        self._init_dynamic_actors()
        self._load_robot_model_actors()
        self._frame_empty_scene()
        self._update_robot_model()

        # Timers
        self.play_timer = QtCore.QTimer(self)
        self.play_timer.setInterval(self.timer_interval_ms)
        self.play_timer.timeout.connect(self._on_frame)
        self.play_timer.start()

        self.rotation_timer = QtCore.QTimer(self)
        self.rotation_timer.setInterval(20)
        self.rotation_timer.timeout.connect(self._on_rotate_frame)
        self.rotation_timer.start()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

        if input_data is not None:
            self.load_data(input_data)

    # ------------------------------------------------------------------
    # Initial actor setup
    # ------------------------------------------------------------------

    def _install_camera_up_stabilizer(self) -> None:
        try:
            interactor = self.plotter.iren
            interactor.add_observer("InteractionEvent", lambda *_: self._stabilize_camera_up())
            interactor.add_observer("EndInteractionEvent", lambda *_: self._stabilize_camera_up())
        except Exception:
            pass

    def _stabilize_camera_up(self) -> None:
        if self._is_shutting_down:
            return
        cam = self.plotter.camera
        position = np.array(cam.position, dtype=float)
        focal = np.array(cam.focal_point, dtype=float)
        up = np.array(cam.up, dtype=float)
        view_dir = focal - position
        view_norm = np.linalg.norm(view_dir)
        up_norm = np.linalg.norm(up)
        if view_norm <= 1e-9 or up_norm <= 1e-9:
            return
        view_dir /= view_norm
        up /= up_norm
        if abs(float(np.dot(view_dir, up))) < 0.98:
            return
        cam.up = self._camera_up_for_view(position, focal)

    def _init_dynamic_actors(self) -> None:
        self.hist_actor = self.plotter.add_mesh(
            self._hist_mesh,
            color="gray",
            opacity=0.35,
            line_width=2,
            name="history_line",
        )
        self.hist_actor.SetVisibility(False)

        self.active_actor = self.plotter.add_mesh(
            self._active_mesh,
            color="red",
            opacity=1.0,
            line_width=2,
            name="active_tail",
        )
        self.active_actor.SetVisibility(False)

        self.head_actor = self.plotter.add_mesh(
            self._head_mesh,
            color="deepskyblue",
            smooth_shading=False,
            lighting=False,
            name="head_point",
        )
        self.head_actor.SetVisibility(False)

    def _refresh_camera_dependent_bounds(self, render: bool = False) -> None:
        self._update_outer_silhouette_actor(render=False)
        if render:
            self.plotter.render()

    # ------------------------------------------------------------------
    # Data loading and bounds creation
    # ------------------------------------------------------------------

    def load_data(self, input_data: DataLike) -> None:
        if isinstance(input_data, pd.DataFrame):
            df = input_data.copy()
        elif isinstance(input_data, dict):
            df = pd.DataFrame(input_data)
        elif isinstance(input_data, str) and input_data.lower().endswith(".csv"):
            df = pd.read_csv(input_data)
        else:
            raise TypeError("load_data expects a pandas DataFrame, dict, or CSV file path.")

        required = {"phi_deg", "r_xy_mm", "z_mm"}
        missing = required.difference(df.columns)
        if missing:
            raise ValueError(f"Input data is missing required columns: {sorted(missing)}")

        self.phi_arr = df["phi_deg"].to_numpy(dtype=float)
        self.N = len(self.phi_arr)
        self.r_m = df["r_xy_mm"].to_numpy(dtype=float) / 1000.0
        self.z_m = df["z_mm"].to_numpy(dtype=float) / 1000.0
        phi_rad = np.radians(self.phi_arr)

        self.x = self.r_m * np.cos(phi_rad)
        self.y = self.r_m * np.sin(phi_rad)
        self.z = self.z_m

        points = np.column_stack((self.x, self.y, self.z)) if self.N else np.empty((0, 3))
        points = points[np.all(np.isfinite(points), axis=1)]

        if len(points) != self.N:
            raise ValueError("Non-finite coordinate values found in input data.")

        logger.debug("Loaded points: {}", points.shape)
        logger.debug("XYZ min: {}", points.min(axis=0) if len(points) else None)
        logger.debug("XYZ max: {}", points.max(axis=0) if len(points) else None)

        self._replace_base_cloud(points)

        if self.N:
            self.curr_idx = min(self.curr_idx, self.N - 1)
            self.exact_idx = float(self.curr_idx)
        else:
            self.curr_idx = 0
            self.exact_idx = 0.0

        self._build_bounds_from_settings(df)
        self._set_axes_equal()
        self.update_plot()
        self.plotter.render()

    def _replace_base_cloud(self, points: np.ndarray) -> None:
        self._base_points = np.asarray(points, dtype=float)
        if self.base_actor is not None:
            try:
                self.plotter.remove_actor("measurement_sphere_cloud", render=False)
            except Exception:
                pass
            self.base_actor = None

        if len(points) == 0:
            return

        glyphs = _make_sphere_glyphs(points, self.cloud_sphere_radius_m)

        mesh_kwargs = dict(
            opacity=self.current_alpha,
            smooth_shading=True,
            lighting=True,
            show_scalar_bar=False,
            name="measurement_sphere_cloud",
        )
        if self.point_color_mode == "single":
            mesh_kwargs["color"] = _hsv_slider_to_rgb01(self.current_color_val)
        else:
            mesh_kwargs.update(
                scalars="z_norm",
                cmap=DARK_BG_ELEVATION_CMAP,
                clim=(0.0, 1.0),
            )

        self.base_actor = self.plotter.add_mesh(glyphs, **mesh_kwargs)

        try:
            self.base_actor.SetPickable(True)
        except Exception:
            pass

    def _build_bounds_from_settings(self, df: pd.DataFrame) -> None:
        self._remove_bounds_actors()
        self.bounds = BoundsInfo()

        settings = _settings_from_df(df)
        if not settings:
            logger.debug("No gen_settings found; skipping cylinder bounds.")
            self._update_bounds_readout(settings)
            return

        r_int = _safe_float(settings.get("cyl_radius_internal"), 0.0)
        if r_int <= 0.0:
            r_int = _safe_float(settings.get("cyl_radius_mm"), 0.0) / 1000.0

        h_int = _safe_float(settings.get("cyl_height_internal"), 0.0)
        if h_int <= 0.0:
            h_int = _safe_float(settings.get("cyl_height_mm"), 0.0) / 1000.0

        r_ext_value = settings.get("cyl_radius_external")
        if r_ext_value is None:
            r_ext = r_int + _safe_float(settings.get("wall_thickness_mm"), 0.0) / 1000.0
        else:
            r_ext = _safe_float(r_ext_value, 0.0)

        h_ext_value = settings.get("cyl_height_external")
        if h_ext_value is None:
            h_ext = h_int + 2.0 * _safe_float(settings.get("wall_thickness_mm"), 0.0) / 1000.0
        else:
            h_ext = _safe_float(h_ext_value, 0.0)

        z_offset_str = settings.get("z_offset_mm", "None")
        if str(z_offset_str).strip().lower() != "none":
            z_center = _safe_float(z_offset_str, 0.0) / 1000.0
        elif settings.get("z_midpoint_zero", "False") == "True":
            z_center = 0.0
        else:
            z_center = _safe_float(settings.get("cyl_height_external"), h_int) / 2.0

        self.bounds = BoundsInfo(
            has_inner=(r_int > 0 and h_int > 0),
            has_outer=(r_ext > 0 and h_ext > 0),
            r_int=r_int,
            h_int=h_int,
            r_ext=r_ext,
            h_ext=h_ext,
            z_center=z_center,
        )

        logger.debug(
            "Bounds parsed: inner={} R={:.4f} H={:.4f} outer={} R={:.4f} H={:.4f} z_center={:.4f}",
            self.bounds.has_inner,
            r_int,
            h_int,
            self.bounds.has_outer,
            r_ext,
            h_ext,
            z_center,
        )

        if self.show_bounds and self.bounds.has_inner:
            inner = pv.Cylinder(
                center=(0.0, 0.0, z_center),
                direction=(0.0, 0.0, 1.0),
                radius=r_int,
                height=h_int,
                resolution=96,
                capping=True,
            )
            self.inner_actor = self.plotter.add_mesh(
                inner,
                color="lightblue",
                opacity=0.2,
                smooth_shading=True,
                name="inner_translucent_cylinder",
            )
            try:
                self.inner_actor.SetPickable(False)
            except Exception:
                pass

        if self.show_bounds and self.bounds.has_outer:
            self._update_outer_silhouette_actor(render=False)
            self.outer_cap_rim_actor = self._add_outer_cap_rims(
                r_ext,
                h_ext,
                z_center,
                color=self._bounds_color(),
                opacity=0.5,
                name="outer_cap_rims",
            )
            self._update_front_back_marker_actor(render=False)

        self._update_bounds_readout(settings)

    def _update_outer_silhouette_actor(self, render: bool = False) -> None:
        try:
            self.plotter.remove_actor("outer_wireframe_cylinder", render=False)
        except Exception:
            pass
        self.outer_actor = None

        if not (self.show_bounds and self.bounds.has_outer):
            return

        radius = self.bounds.r_ext
        z_min = self.bounds.z_center - self.bounds.h_ext / 2.0
        z_max = self.bounds.z_center + self.bounds.h_ext / 2.0
        right = self._screen_right_xy()
        tube_radius = max(radius * 0.009, 0.0018)
        meshes: list[pv.PolyData] = []

        for sign in (-1.0, 1.0):
            xy = right[:2] * radius * sign
            mesh = self._segment_cylinder(
                (float(xy[0]), float(xy[1]), z_min),
                (float(xy[0]), float(xy[1]), z_max),
                tube_radius,
                resolution=8,
            )
            if mesh is not None:
                meshes.append(mesh)

        combined = self._combine_meshes(meshes)
        if combined is None:
            return

        self.outer_actor = self.plotter.add_mesh(
            combined,
            color=self._bounds_color(),
            opacity=0.85,
            smooth_shading=True,
            name="outer_wireframe_cylinder",
        )
        self.outer_actor.SetVisibility(self.show_bounds)
        try:
            self.outer_actor.SetPickable(False)
        except Exception:
            pass
        if render:
            self.plotter.render()

    def _add_outer_cap_rims(
        self,
        radius: float,
        height: float,
        z_center: float,
        color: str,
        opacity: float,
        name: str,
    ):
        z_min = z_center - height / 2.0
        z_max = z_center + height / 2.0
        theta = np.linspace(0.0, 2.0 * np.pi, 97)
        rim_radius = max(radius * 0.006, 0.0012)
        meshes: list[pv.PolyData] = []

        for z_val in (z_min, z_max):
            points = [
                np.array([radius * np.cos(t), radius * np.sin(t), z_val], dtype=float)
                for t in theta
            ]
            for p0, p1 in zip(points[:-1], points[1:]):
                mesh = self._segment_cylinder(p0, p1, rim_radius, resolution=6)
                if mesh is not None:
                    meshes.append(mesh)

        combined = self._combine_meshes(meshes)
        if combined is None:
            return None

        actor = self.plotter.add_mesh(
            combined,
            color=color,
            opacity=opacity,
            smooth_shading=True,
            name=name,
        )
        actor.SetVisibility(self.show_bounds)
        try:
            actor.SetPickable(False)
        except Exception:
            pass
        return actor

    def _update_front_back_marker_actor(self, render: bool = False) -> None:
        for actor_name in ("bottom_front_back_ticks", "bottom_front_back_labels"):
            try:
                self.plotter.remove_actor(actor_name, render=False)
            except Exception:
                pass
        self.front_back_marker_actor = None
        self.front_back_label_actor = None

        if not (self.show_bounds and self.bounds.has_outer):
            return

        radius = self.bounds.r_ext
        z_min = self.bounds.z_center - self.bounds.h_ext / 2.0
        front = np.array([1.0, 0.0], dtype=float)
        back = np.array([-1.0, 0.0], dtype=float)
        tube_radius = max(radius * 0.007, 0.0014)
        tick_inner = radius * 0.96
        tick_outer = radius * 1.14
        label_radius = radius * 1.25
        label_z = z_min + max(radius * 0.01, 0.002)
        label_height = max(radius * 0.11, 0.018)

        meshes: list[pv.PolyData] = []
        label_meshes: list[pv.PolyData] = []
        for label, direction, angle_deg in (("Front", front, 90.0), ("Back", back, 270.0)):
            p0 = np.array([direction[0] * tick_inner, direction[1] * tick_inner, z_min], dtype=float)
            p1 = np.array([direction[0] * tick_outer, direction[1] * tick_outer, z_min], dtype=float)
            mesh = self._segment_cylinder(p0, p1, tube_radius, resolution=8)
            if mesh is not None:
                meshes.append(mesh)
            label_mesh = self._make_bottom_label_mesh(
                label,
                (
                    float(direction[0] * label_radius),
                    float(direction[1] * label_radius),
                    float(label_z),
                ),
                label_height,
                angle_deg,
            )
            if label_mesh is not None:
                label_meshes.append(label_mesh)

        combined = self._combine_meshes(meshes)
        if combined is not None:
            self.front_back_marker_actor = self.plotter.add_mesh(
                combined,
                color="#8fc7ff",
                opacity=0.95,
                smooth_shading=True,
                name="bottom_front_back_ticks",
            )
            self.front_back_marker_actor.SetVisibility(self.show_bounds)
            try:
                self.front_back_marker_actor.SetPickable(False)
            except Exception:
                pass

        combined_labels = self._combine_meshes(label_meshes)
        if combined_labels is not None:
            self.front_back_label_actor = self.plotter.add_mesh(
                combined_labels,
                color=self._bounds_color(),
                opacity=1.0,
                smooth_shading=False,
                lighting=False,
                name="bottom_front_back_labels",
            )
            self.front_back_label_actor.SetVisibility(self.show_bounds)
            try:
                self.front_back_label_actor.SetPickable(False)
            except Exception:
                pass
        if render:
            self.plotter.render()

    def _make_bottom_label_mesh(
        self,
        text: str,
        center: tuple[float, float, float],
        height: float,
        angle_deg: float,
    ) -> pv.PolyData | None:
        try:
            mesh = pv.Text3D(
                text,
                depth=0.0,
                height=height,
                center=(0.0, 0.0, 0.0),
                normal=(0.0, 0.0, 1.0),
            )
            mesh.rotate_z(angle_deg, point=(0.0, 0.0, 0.0), inplace=True)
            mesh.translate(center, inplace=True)
            return mesh
        except Exception:
            return None

    def _remove_bounds_actors(self) -> None:
        for actor_name in (
            "inner_translucent_cylinder",
            "outer_wireframe_cylinder",
            "outer_cap_rims",
            "bottom_front_back_ticks",
            "bottom_front_back_labels",
            "equal_aspect_wire_box",
        ):
            try:
                self.plotter.remove_actor(actor_name, render=False)
            except Exception:
                pass
        self.inner_actor = None
        self.outer_actor = None
        self.outer_cap_rim_actor = None
        self.front_back_marker_actor = None
        self.front_back_label_actor = None
        self.bounds_box_actor = None

    def _update_bounds_readout(self, settings: dict[str, str]) -> None:
        self._last_settings = settings

    # ------------------------------------------------------------------
    # External control API
    # ------------------------------------------------------------------

    def set_current_index(self, idx: int) -> None:
        if self.is_playing or self.N == 0:
            return
        idx = max(0, min(int(idx), self.N - 1))
        if idx != self.curr_idx:
            self.curr_idx = idx
            self.exact_idx = float(idx)
            self.update_plot()

    def play(self) -> None:
        self.is_playing = True

    def pause(self) -> None:
        self.is_playing = False

    def rewind(self) -> None:
        self.pause()
        self.curr_idx = 0
        self.exact_idx = 0.0
        self.update_plot()

    def step_fwd(self) -> None:
        self.pause()
        if self.curr_idx < self.N - 1:
            self.curr_idx += 1
            self.exact_idx = float(self.curr_idx)
            self.update_plot()

    def step_back(self) -> None:
        self.pause()
        if self.curr_idx > 0:
            self.curr_idx -= 1
            self.exact_idx = float(self.curr_idx)
            self.update_plot()

    def set_speed(self, ppm: float) -> None:
        if ppm > 0:
            self.ppm = float(ppm)

    def set_tail_length(self, val: int) -> None:
        self.tail_length = max(0, int(val))
        if not self.is_playing:
            self.update_plot()

    def set_history_mode(self, enabled: bool) -> None:
        self.use_history_fading = bool(enabled)
        if not self.is_playing:
            self.update_plot()

    def set_ortho(self, enabled: bool) -> None:
        self.use_ortho = bool(enabled)
        self.plotter.camera.parallel_projection = self.use_ortho
        self.plotter.render()

    def set_view(self, elev: float, azim: float) -> None:
        if self._is_shutting_down:
            return
        elev = self._stable_elevation(elev)
        if self.N:
            center = np.array([
                0.5 * (self.x.min() + self.x.max()),
                0.5 * (self.y.min() + self.y.max()),
                0.5 * (self.z.min() + self.z.max()),
            ])
            radius = max(
                float(np.ptp(self.x)),
                float(np.ptp(self.y)),
                float(np.ptp(self.z)),
                1e-3,
            ) * 2.0
        else:
            center = np.array([0.0, 0.0, 0.0])
            radius = 2.0

        el = np.radians(elev)
        az = np.radians(azim)
        pos = center + radius * np.array([
            np.cos(el) * np.cos(az),
            np.cos(el) * np.sin(az),
            np.sin(el),
        ])
        self.plotter.camera.position = tuple(pos)
        self.plotter.camera.focal_point = tuple(center)
        self.plotter.camera.up = self._camera_up_for_view(pos, center)
        self._refresh_camera_dependent_bounds()
        self.plotter.render()

    def _stable_elevation(self, elev: float) -> float:
        elev = float(elev)
        if abs(abs(elev) - 90.0) <= 1e-6:
            return 89.0 if elev >= 0.0 else -89.0
        return elev

    def _camera_up_for_view(self, position, focal_point) -> tuple[float, float, float]:
        view_dir = np.asarray(focal_point, dtype=float) - np.asarray(position, dtype=float)
        norm = np.linalg.norm(view_dir)
        if norm <= 1e-9:
            return (0.0, 0.0, 1.0)
        view_dir /= norm
        if abs(float(np.dot(view_dir, np.array([0.0, 0.0, 1.0], dtype=float)))) >= 0.999:
            return (0.0, 1.0, 0.0)
        return (0.0, 0.0, 1.0)

    def set_alpha(self, val: float) -> None:
        self.current_alpha = max(0.0, min(1.0, float(val)))
        if self.base_actor is not None:
            self.base_actor.GetProperty().SetOpacity(self.current_alpha)
        self.plotter.render()

    def set_color(self, val: float) -> None:
        self.current_color_val = max(0.0, min(1.0, float(val)))
        if self.point_color_mode == "single" and self.base_actor is not None:
            self.base_actor.GetProperty().SetColor(_hsv_slider_to_rgb01(self.current_color_val))
        self.plotter.render()

    def set_point_color_mode(self, mode: str) -> None:
        mode = str(mode).strip().lower()
        if mode not in {"elevation", "single"}:
            mode = "elevation"
        if mode == self.point_color_mode:
            return
        self.point_color_mode = mode
        if len(self._base_points):
            self._replace_base_cloud(self._base_points)
            self.update_plot()
        self.plotter.render()

    def set_bounds_visibility(self, visible: bool) -> None:
        self.show_bounds = bool(visible)
        if self.show_bounds and self.bounds.has_outer:
            self._update_outer_silhouette_actor(render=False)
            self._update_front_back_marker_actor(render=False)
        for actor in (
            self.inner_actor,
            self.outer_actor,
            self.outer_cap_rim_actor,
            self.front_back_marker_actor,
            self.front_back_label_actor,
        ):
            if actor is not None:
                actor.SetVisibility(self.show_bounds)
        self.plotter.render()

    def set_grid_visibility(self, visible: bool) -> None:
        self.show_grid = bool(visible)
        try:
            self.plotter.remove_bounds_axes()
        except Exception:
            pass
        if self.show_grid and self._camera_bounds is not None:
            self._show_back_grid(self._camera_bounds)
        self.plotter.render()

    def set_white_background(self, enabled: bool) -> None:
        self.use_white_background = bool(enabled)
        self._set_plot_background()
        self._update_viewport_chrome()
        self._apply_scene_foreground()
        if self.show_grid and self._camera_bounds is not None:
            try:
                self.plotter.remove_bounds_axes()
            except Exception:
                pass
            self._show_back_grid(self._camera_bounds)
        self.plotter.render()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.plotter.interactor and event.type() == QtCore.QEvent.Type.Resize:
            QtCore.QTimer.singleShot(0, self._apply_viewport_mask)
        return super().eventFilter(watched, event)

    def toggle_readout(self, force_state: Optional[bool] = None) -> None:
        self.show_readout = (not self.show_readout) if force_state is None else bool(force_state)
        self.readout_label.setVisible(self.show_readout)
        if self.show_readout:
            self.readout_label.setMaximumHeight(16777215)
            self.readout_label.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
        else:
            self.readout_label.setMaximumHeight(0)
            self.readout_label.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Ignored,
                QtWidgets.QSizePolicy.Policy.Ignored,
            )
        self.readout_label.updateGeometry()
        self.plotter.interactor.updateGeometry()
        self.updateGeometry()
        self.layout().activate()

    def set_rotation_speed(self, deg_per_sec: float) -> None:
        self.rotation_speed_deg_per_sec = max(0.1, float(deg_per_sec or 5.0))

    def start_rotation(self, angle: float = 45.0) -> None:
        self.is_rotating = True
        self.rot_full_angle = float(angle)
        self.rot_target_angle = self.rot_full_angle / 2.0
        self.rot_dir = 1
        self.rot_accumulated = 0.0

    def stop_rotation(self) -> None:
        self.is_rotating = False

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def update_plot(self) -> None:
        if self._is_shutting_down:
            return
        if self.N == 0:
            for actor in (self.head_actor, self.active_actor, self.hist_actor):
                if actor is not None:
                    actor.SetVisibility(False)
            self.readout_label.setText("")
            self._update_robot_model()
            self.plotter.render()
            return

        i = max(0, min(int(self.curr_idx), self.N - 1))
        start_active = max(0, i - self.tail_length) if self.use_history_fading else 0

        head_center = (float(self.x[i]), float(self.y[i]), float(self.z[i]))
        self._head_mesh = _sphere_at(head_center, self.head_sphere_radius_m)
        self.head_actor.mapper.SetInputData(self._head_mesh)
        self.head_actor.SetVisibility(True)

        active_points = np.column_stack((
            self.x[start_active:i + 1],
            self.y[start_active:i + 1],
            self.z[start_active:i + 1],
        ))
        self._active_mesh = _polyline_from_points(active_points)
        self.active_actor.mapper.SetInputData(self._active_mesh)
        self.active_actor.SetVisibility(len(active_points) >= 2)

        if self.use_history_fading and start_active > 0:
            hist_points = np.column_stack((
                self.x[:start_active],
                self.y[:start_active],
                self.z[:start_active],
            ))
        else:
            hist_points = np.empty((0, 3))
        self._hist_mesh = _polyline_from_points(hist_points)
        self.hist_actor.mapper.SetInputData(self._hist_mesh)
        self.hist_actor.SetVisibility(self.use_history_fading and start_active > 0)

        self._update_readout()
        self._update_robot_model()
        self.plotter.render()

    def _update_readout(self) -> None:
        if not self.show_readout or self.N == 0:
            return

        i = self.curr_idx
        lines: list[str] = []
        for offset in range(-1, 3):
            idx = i + offset
            if 0 <= idx < self.N:
                prefix = "-&gt;" if offset == 0 else "&nbsp;&nbsp;"
                lines.append(
                    f"{prefix}{idx:4d} | "
                    f"<b>R</b>{self.r_m[idx] * 1000:6.1f}  "
                    f"<b>P</b>{self.phi_arr[idx]:6.1f}  "
                    f"<b>Z</b>{self.z[idx] * 1000:6.1f}"
                )
            else:
                lines.append("&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;---")

        if self.bounds.has_inner or self.bounds.has_outer:
            b = self.bounds
            lines.append("<br>")
            lines.append(
                f"<b>Inner</b>: R{b.r_int * 1000:6.1f} H{b.h_int * 1000:6.1f} mm&nbsp;&nbsp;"
                f"<b>Outer</b>: R{b.r_ext * 1000:6.1f} H{b.h_ext * 1000:6.1f} mm"
            )

        self.readout_label.setText("<pre style='margin:0'>" + "\n".join(lines) + "</pre>")

    def _on_frame(self) -> None:
        if self._is_shutting_down:
            return
        if self.is_playing and self.N > 0:
            step = (self.ppm / 60.0) * (self.timer_interval_ms / 1000.0)
            self.exact_idx += step
            new_idx = int(self.exact_idx)

            if new_idx > self.curr_idx:
                if new_idx >= self.N:
                    self.curr_idx = self.N - 1
                    self.exact_idx = float(self.curr_idx)
                    self.pause()
                else:
                    self.curr_idx = new_idx
                self.update_plot()
            else:
                self._update_robot_model()
                self.plotter.render()

    def _robot_model_dir(self) -> Path | None:
        candidates = [
            Path.cwd() / "hals_3d_model",
            Path.cwd() / "hald_3d_model",
            Path(__file__).resolve().parents[2] / "hals_3d_model",
            Path(__file__).resolve().parents[2] / "hald_3d_model",
        ]
        for path in candidates:
            if path.exists():
                return path
        return None

    def _load_robot_model_actors(self) -> None:
        model_dir = self._robot_model_dir()
        if model_dir is None:
            return

        files = {
            "phi": model_dir / "p_phi.obj",
            "r": model_dir / "r_radius.obj",
            "z": model_dir / "z_height.obj",
        }
        if not all(path.exists() for path in files.values()):
            logger.warning("Robot model folder found, but expected OBJ files are missing: {}", model_dir)
            return

        try:
            phi_mesh = self._read_robot_mesh(files["phi"])
            r_mesh = self._read_robot_mesh(files["r"])
            z_mesh = self._read_robot_mesh(files["z"])
        except Exception as exc:
            logger.warning("Could not load robot model from {}: {}", model_dir, exc)
            return

        self.robot_phi_actor = self.plotter.add_mesh(
            phi_mesh,
            color="#60a5fa",
            opacity=0.5,
            smooth_shading=True,
            lighting=True,
            ambient=0.42,
            diffuse=0.7,
            specular=0.12,
            name="robot_phi_axis",
        )
        self.robot_r_actor = self.plotter.add_mesh(
            r_mesh,
            color="#68FF6F",
            opacity=0.3,
            smooth_shading=True,
            lighting=True,
            ambient=0.42,
            diffuse=0.7,
            specular=0.12,        
            name="robot_r_axis",
        )
        self.robot_z_actor = self.plotter.add_mesh(
            z_mesh,
            color="#fbbf24",
            opacity=0.6,
            smooth_shading=True,
            lighting=True,
            ambient=0.42,
            diffuse=0.7,
            specular=0.12,
            name="robot_z_axis",
        )

        for actor in (self.robot_phi_actor, self.robot_r_actor, self.robot_z_actor):
            actor.SetVisibility(False)
            try:
                actor.SetPickable(False)
            except Exception:
                pass
        self.robot_model_loaded = True
        logger.debug("Loaded robot model from {}", model_dir)

    def _read_robot_mesh(self, path: Path) -> pv.PolyData:
        mesh = pv.read(path)
        if isinstance(mesh, pv.MultiBlock):
            mesh = mesh.combine()
        return mesh.triangulate()

    def _interpolated_motion_values(self) -> tuple[float, float, float]:
        if self.N == 0:
            return 0.0, 0.0, 0.0
        exact = max(0.0, min(float(self.exact_idx), float(self.N - 1)))
        low = int(np.floor(exact))
        high = min(low + 1, self.N - 1)
        t = exact - low
        phi0 = float(self.phi_arr[low])
        phi1 = float(self.phi_arr[high])
        phi_delta = ((phi1 - phi0 + 180.0) % 360.0) - 180.0
        phi = phi0 + phi_delta * t
        r = float(self.r_m[low] + (self.r_m[high] - self.r_m[low]) * t)
        z = float(self.z[low] + (self.z[high] - self.z[low]) * t)
        return phi, r, z

    def _robot_origin_z(self) -> float:
        # Robot/STL meshes are authored with their datum at the top of the DUT
        # stool. Keep that datum fixed at viewer Z=0; generated grid coordinates
        # are already loaded in absolute metres, so negative z_mm values should
        # render below the stool top instead of lifting the robot to grid z_min.
        return 0.0

    def _update_robot_model(self) -> None:
        if not self.robot_model_loaded:
            return

        phi_deg, radius_m, z_m = self._interpolated_motion_values()
        origin_z = self._robot_origin_z()
        z_axis_m = z_m - origin_z

        root = self._translate(0.0, 0.0, origin_z) @ self._rotate_z(phi_deg)
        obj_to_viewer = self._robot_obj_to_viewer_matrix()
        phi_stage = root @ obj_to_viewer
        r_stage = root @ self._translate(radius_m, 0.0, 0.0) @ obj_to_viewer
        z_stage = root @ self._translate(radius_m, 0.0, 0.0) @ self._translate(0.0, 0.0, z_axis_m) @ obj_to_viewer

        self._set_actor_matrix(self.robot_phi_actor, phi_stage)
        self._set_actor_matrix(self.robot_r_actor, r_stage)
        self._set_actor_matrix(self.robot_z_actor, z_stage)
        for actor in (self.robot_phi_actor, self.robot_r_actor, self.robot_z_actor):
            if actor is not None:
                actor.SetVisibility(True)

    def _robot_obj_to_viewer_matrix(self) -> np.ndarray:
        # OBJ model is in millimetres with CAD +Y as radial and CAD +Z as height.
        # Viewer phi=0 points along +X; the hierarchy adds origin/axis motion.
        return np.array(
            [
                [0.0, ROBOT_MODEL_UNITS_TO_METERS, 0.0, 0.0],
                [ROBOT_MODEL_UNITS_TO_METERS, 0.0, 0.0, 0.0],
                [0.0, 0.0, ROBOT_MODEL_UNITS_TO_METERS, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=float,
        )

    def _translate(self, x: float, y: float, z: float) -> np.ndarray:
        matrix = np.eye(4, dtype=float)
        matrix[:3, 3] = [float(x), float(y), float(z)]
        return matrix

    def _rotate_z(self, angle_deg: float) -> np.ndarray:
        angle = np.radians(angle_deg)
        c = float(np.cos(angle))
        s = float(np.sin(angle))
        return np.array(
            [
                [c, -s, 0.0, 0.0],
                [s, c, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=float,
        )

    def _set_actor_matrix(self, actor, matrix: np.ndarray) -> None:
        if actor is None:
            return
        actor.SetUserMatrix(pv.vtkmatrix_from_array(matrix))

    def _on_rotate_frame(self) -> None:
        if self._is_shutting_down:
            return
        if not self.is_rotating:
            return

        dt = self.rotation_timer.interval() / 1000.0
        step = self.rotation_speed_deg_per_sec * dt
        self._rotate_camera_about_z(step * self.rot_dir)
        self.rot_accumulated += step

        if self.rot_accumulated >= self.rot_target_angle:
            self.rot_dir *= -1
            self.rot_accumulated = 0.0
            self.rot_target_angle = self.rot_full_angle

        self.plotter.render()

    def _rotate_camera_about_z(self, angle_deg: float) -> None:
        cam = self.plotter.camera
        pos = np.array(cam.position, dtype=float)
        focal = np.array(cam.focal_point, dtype=float)
        up = np.array(cam.up, dtype=float)
        rel = pos - focal

        a = np.radians(angle_deg)
        rot = np.array([
            [np.cos(a), -np.sin(a), 0.0],
            [np.sin(a),  np.cos(a), 0.0],
            [0.0,        0.0,       1.0],
        ])
        new_pos = focal + rot @ rel
        cam.position = tuple(new_pos)
        cam.focal_point = tuple(focal)
        cam.up = tuple(rot @ up)
        self._refresh_camera_dependent_bounds()

    def shutdown(self) -> None:
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        self.is_playing = False
        self.is_rotating = False
        for timer_name in ("play_timer", "rotation_timer"):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass
        try:
            self.plotter.close()
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)

    def _compute_camera_bounds(self) -> tuple[float, float, float, float, float, float]:
        xs = [float(self.x.min()), float(self.x.max())]
        ys = [float(self.y.min()), float(self.y.max())]
        zs = [float(self.z.min()), float(self.z.max()), 0.0]

        b = self.bounds
        if b.has_outer:
            xs.extend([-b.r_ext, b.r_ext])
            ys.extend([-b.r_ext, b.r_ext])
            zs.extend([b.z_center - b.h_ext / 2.0, b.z_center + b.h_ext / 2.0])
        elif b.has_inner:
            xs.extend([-b.r_int, b.r_int])
            ys.extend([-b.r_int, b.r_int])
            zs.extend([b.z_center - b.h_int / 2.0, b.z_center + b.h_int / 2.0])

        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        zmin, zmax = min(zs), max(zs)

        center = np.array([
            0.5 * (xmin + xmax),
            0.5 * (ymin + ymax),
            0.5 * (zmin + zmax),
        ])
        span = max(xmax - xmin, ymax - ymin, zmax - zmin, 1e-3)
        half = span * 0.58

        return (
            center[0] - half, center[0] + half,
            center[1] - half, center[1] + half,
            center[2] - half, center[2] + half,
        )

    def _grid_radius(self) -> float:
        if self.bounds.has_outer:
            return self.bounds.r_ext
        if self.bounds.has_inner:
            return self.bounds.r_int
        if self.N:
            return max(float(np.max(self.r_m)), 1e-3)
        return 1.0

    def _floor_grid_radius(self) -> float:
        base_radius = self._grid_radius()
        if self._camera_bounds is not None:
            xmin, xmax, ymin, ymax, _, _ = self._camera_bounds
            visible_radius = 0.5 * max(xmax - xmin, ymax - ymin)
        else:
            visible_radius = base_radius
        return max(base_radius * 4.0, visible_radius * 1.85)

    def _grid_z_limits(self) -> tuple[float, float]:
        if self.bounds.has_outer:
            return (
                self.bounds.z_center - self.bounds.h_ext / 2.0,
                self.bounds.z_center + self.bounds.h_ext / 2.0,
            )
        if self.bounds.has_inner:
            return (
                self.bounds.z_center - self.bounds.h_int / 2.0,
                self.bounds.z_center + self.bounds.h_int / 2.0,
            )
        if self.N:
            return float(np.min(self.z)), float(np.max(self.z))
        return -0.5, 0.5

    def _segment_cylinder(self, p0, p1, radius: float, resolution: int = 6) -> pv.PolyData | None:
        p0 = np.asarray(p0, dtype=float)
        p1 = np.asarray(p1, dtype=float)
        vec = p1 - p0
        length = float(np.linalg.norm(vec))
        if length <= 1e-9:
            return None
        return pv.Cylinder(
            center=tuple((p0 + p1) * 0.5),
            direction=tuple(vec / length),
            radius=radius,
            height=length,
            resolution=resolution,
            capping=True,
        )

    def _combine_meshes(self, meshes: list[pv.PolyData]) -> pv.PolyData | None:
        meshes = [mesh for mesh in meshes if mesh is not None and mesh.n_points > 0]
        if not meshes:
            return None
        combined = meshes[0]
        for mesh in meshes[1:]:
            combined = combined.merge(mesh, merge_points=False)
        return combined

    def _show_back_grid(self, bounds) -> None:
        self.plotter.show_bounds(
            bounds=bounds,
            grid="back",
            location="outer",
            all_edges=False,
            xtitle="X (m)",
            ytitle="Y (m)",
            ztitle="Z (m)",
            color=self._bounds_color(),
        )

    def _background_color(self) -> str:
        return "white" if self.use_white_background else "#0e1420"

    def _background_top_color(self) -> str:
        return "#f8fafc" if self.use_white_background else "#23304a"

    def _set_plot_background(self) -> None:
        self.plotter.set_background(self._background_color(), top=self._background_top_color())

    def _bounds_color(self) -> str:
        return "black" if self.use_white_background else "white"

    def _bounds_rgb(self) -> tuple[float, float, float]:
        return (0.0, 0.0, 0.0) if self.use_white_background else (1.0, 1.0, 1.0)

    def _apply_scene_foreground(self) -> None:
        rgb = self._bounds_rgb()
        for actor in (
            self.outer_actor,
            self.outer_cap_rim_actor,
            self.front_back_label_actor,
            self.cylindrical_grid_actor if hasattr(self, "cylindrical_grid_actor") else None,
            self.floor_label_actor if hasattr(self, "floor_label_actor") else None,
        ):
            if actor is None:
                continue
            try:
                actor.GetProperty().SetColor(rgb)
            except Exception:
                pass

    def _install_viewport_chrome(self) -> None:
        self._viewport_radius = 8
        interactor = self.plotter.interactor
        interactor.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        interactor.installEventFilter(self)
        self._update_viewport_chrome()
        QtCore.QTimer.singleShot(0, self._apply_viewport_mask)

    def _update_viewport_chrome(self) -> None:
        fill = "#ffffff" if self.use_white_background else "#0e1420"
        border = "#d7dee8" if self.use_white_background else "#cbd5e1"
        self.plotter.interactor.setStyleSheet(
            "QWidget {"
            f"background: {fill};"
            f"border: 1px solid {border};"
            f"border-radius: {self._viewport_radius}px;"
            "}"
        )

    def _apply_viewport_mask(self) -> None:
        interactor = self.plotter.interactor
        rect = QtCore.QRectF(interactor.rect())
        if rect.width() <= 0 or rect.height() <= 0:
            return
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, self._viewport_radius, self._viewport_radius)
        interactor.setMask(QtGui.QRegion(path.toFillPolygon().toPolygon()))

    def _add_point_labels(self, points: np.ndarray, labels: list[str], name: str, color: str, font_size: int):
        try:
            self.plotter.remove_actor(name, render=False)
        except Exception:
            pass
        if len(points) == 0:
            return None
        try:
            actor = self.plotter.add_point_labels(
                points,
                labels,
                name=name,
                font_size=font_size,
                text_color=color,
                point_size=0,
                shape_opacity=0.0,
                always_visible=True,
                render=False,
            )
        except TypeError:
            actor = self.plotter.add_point_labels(
                points,
                labels,
                name=name,
                font_size=font_size,
                text_color=color,
                always_visible=True,
                render=False,
            )
        if actor is not None:
            try:
                actor.SetVisibility(self.show_bounds)
            except Exception:
                pass
        return actor

    def _build_bottom_radial_grid_actor(self) -> None:
        try:
            self.plotter.remove_actor("bottom_radial_grid", render=False)
        except Exception:
            pass
        try:
            self.plotter.remove_actor("bottom_radial_grid_labels", render=False)
        except Exception:
            pass
        self.floor_label_actor = None

        radius = self._floor_grid_radius()
        z_min, _ = self._grid_z_limits()
        tube_radius = max(radius * 0.004, 0.001)
        theta = np.linspace(0.0, 2.0 * np.pi, 49)
        meshes: list[pv.PolyData] = []
        label_points: list[tuple[float, float, float]] = []
        labels: list[str] = []
        ring_values = np.linspace(radius / 6.0, radius, 6)

        for r_val in ring_values:
            ring_points = [
                np.array([r_val * np.cos(t), r_val * np.sin(t), z_min], dtype=float)
                for t in theta
            ]
            for p0, p1 in zip(ring_points[:-1], ring_points[1:]):
                mesh = self._segment_cylinder(p0, p1, tube_radius, resolution=6)
                if mesh is not None:
                    meshes.append(mesh)
            label_angle = np.radians(25.0)
            label_points.append((
                float(r_val * np.cos(label_angle)),
                float(r_val * np.sin(label_angle)),
                float(z_min),
            ))
            labels.append(f"R {r_val * 1000:.0f} mm")

        for angle in np.linspace(0.0, 2.0 * np.pi, 12, endpoint=False):
            p0 = (0.0, 0.0, z_min)
            p1 = (radius * np.cos(angle), radius * np.sin(angle), z_min)
            mesh = self._segment_cylinder(p0, p1, tube_radius, resolution=6)
            if mesh is not None:
                meshes.append(mesh)

        combined = self._combine_meshes(meshes)
        if combined is None:
            self.cylindrical_grid_actor = None
            return

        self.cylindrical_grid_actor = self.plotter.add_mesh(
            combined,
            color=self._bounds_color(),
            opacity=0.35,
            smooth_shading=False,
            name="bottom_radial_grid",
        )
        self.cylindrical_grid_actor.SetVisibility(self.show_bounds)
        try:
            self.cylindrical_grid_actor.SetPickable(False)
        except Exception:
            pass
        self.floor_label_actor = self._add_point_labels(
            np.asarray(label_points, dtype=float),
            labels,
            name="bottom_radial_grid_labels",
            color=self._bounds_color(),
            font_size=11,
        )

    def _screen_right_xy(self) -> np.ndarray:
        cam = self.plotter.camera
        pos = np.array(cam.position, dtype=float)
        focal = np.array(cam.focal_point, dtype=float)
        up = np.array(cam.up, dtype=float)
        view_dir = focal - pos
        view_norm = np.linalg.norm(view_dir)
        if view_norm <= 1e-9:
            return np.array([1.0, 0.0, 0.0], dtype=float)
        view_dir /= view_norm
        right = np.cross(view_dir, up)
        right[2] = 0.0
        right_norm = np.linalg.norm(right)
        if right_norm <= 1e-6:
            return np.array([1.0, 0.0, 0.0], dtype=float)
        return right / right_norm

    def _update_z_tape_actor(self, render: bool = False) -> None:
        try:
            self.plotter.remove_actor("left_z_tape", render=False)
        except Exception:
            pass
        try:
            self.plotter.remove_actor("left_z_tape_labels", render=False)
        except Exception:
            pass
        self.z_tape_actor = None
        self.z_tape_label_actor = None

        if self.N == 0:
            return

        radius = self._floor_grid_radius()
        z_min, z_max = self._grid_z_limits()
        if z_max <= z_min:
            return

        center_xy = np.array([
            0.5 * (float(np.min(self.x)) + float(np.max(self.x))),
            0.5 * (float(np.min(self.y)) + float(np.max(self.y))),
        ])
        right = self._screen_right_xy()
        anchor_xy = center_xy - right[:2] * radius * 1.45
        tube_radius = max(radius * 0.007, 0.0015)
        tick_len = radius * 0.12

        meshes: list[pv.PolyData] = []
        label_points: list[tuple[float, float, float]] = []
        labels: list[str] = []
        vertical = self._segment_cylinder(
            (anchor_xy[0], anchor_xy[1], z_min),
            (anchor_xy[0], anchor_xy[1], z_max),
            tube_radius,
            resolution=8,
        )
        if vertical is not None:
            meshes.append(vertical)

        for z_val in np.linspace(z_min, z_max, 7):
            p0 = np.array([anchor_xy[0], anchor_xy[1], z_val], dtype=float)
            p1 = p0 + np.array([right[0], right[1], 0.0], dtype=float) * tick_len
            mesh = self._segment_cylinder(p0, p1, tube_radius, resolution=8)
            if mesh is not None:
                meshes.append(mesh)
            label_pos = p1 + np.array([right[0], right[1], 0.0], dtype=float) * tick_len * 0.18
            label_points.append((float(label_pos[0]), float(label_pos[1]), float(label_pos[2])))
            labels.append(f"{z_val * 1000:.0f} mm")

        combined = self._combine_meshes(meshes)
        if combined is None:
            return
        self.z_tape_actor = self.plotter.add_mesh(
            combined,
            color="gold",
            opacity=0.95,
            smooth_shading=True,
            name="left_z_tape",
        )
        self.z_tape_actor.SetVisibility(self.show_bounds)
        try:
            self.z_tape_actor.SetPickable(False)
        except Exception:
            pass
        self.z_tape_label_actor = self._add_point_labels(
            np.asarray(label_points, dtype=float),
            labels,
            name="left_z_tape_labels",
            color="gold",
            font_size=11,
        )
        if render:
            self.plotter.render()

    def _set_axes_equal(self) -> None:
        if self.N == 0:
            self._frame_empty_scene()
            self._update_robot_model()
            return

        bounds = self._compute_camera_bounds()
        self._camera_bounds = bounds

        try:
            self.plotter.remove_actor("equal_aspect_wire_box", render=False)
        except Exception:
            pass

        self.bounds_box_actor = None

        try:
            self.plotter.remove_bounds_axes()
        except Exception:
            pass
        if self.show_grid:
            self._show_back_grid(bounds)

        self.plotter.enable_parallel_projection() if self.use_ortho else self.plotter.disable_parallel_projection()
        self.plotter.reset_camera(bounds=bounds)
        self.plotter.camera.up = self._camera_up_for_view(
            self.plotter.camera.position,
            self.plotter.camera.focal_point,
        )
        self._refresh_camera_dependent_bounds()
        self.plotter.camera.zoom(1.05)

    def _frame_empty_scene(self) -> None:
        bounds = (-0.35, 0.35, -0.35, 0.35, -0.05, 0.55)
        self._camera_bounds = bounds
        try:
            self.plotter.remove_bounds_axes()
        except Exception:
            pass
        if self.show_grid:
            self._show_back_grid(bounds)
        self.plotter.enable_parallel_projection() if self.use_ortho else self.plotter.disable_parallel_projection()
        self.plotter.reset_camera(bounds=bounds)
        self.plotter.camera.up = self._camera_up_for_view(
            self.plotter.camera.position,
            self.plotter.camera.focal_point,
        )
        self.plotter.camera.zoom(1.05)
