#!/usr/bin/env python3
"""
NiceGUI Interactive 3D Replay Viewer Engine (Plotly Edition)
-------------------------------------------------
This module provides a rendering engine for visualizing 3D coordinate grids 
and paths using NiceGUI and Plotly.

Features:
- WebGL-accelerated 3D scientific visualization (perfect circles, axis grids).
- Animated playback (scrubbing, play, pause, rewind).
- Auto-rotating camera views. Path history tail.
- Drop-in replacement API (methods like `play()`, `pause()`, `set_speed()`).
"""

import json
import time
import colorsys

import numpy as np
import pandas as pd
from nicegui import ui
import plotly.graph_objects as go


def _hsv_slider_to_hex(value):
    value = max(0.0, min(1.0, float(value)))
    if value <= 0.0:
        return '#000000'
    r, g, b = colorsys.hsv_to_rgb(value % 1.0, 1.0, 1.0)
    return f'#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}'

class CoordViewerEngine:
    """
    Pure rendering engine built on NiceGUI and Plotly. 
    Expects input data as a pandas DataFrame, dict, or CSV path.
    """
    def __init__(self, input_data=None):
        # --- Playback State ---
        self.curr_idx = 0
        self.exact_idx = 0.0
        self.is_playing = False
        self.ppm = 600.0
        self.tail_length = 50
        self.use_history_fading = False
        self.use_ortho = False 
        self.timer_interval_ms = 50
        self.show_readout = True

        self.show_bounds = True
        self.has_inner_bounds = False
        self.has_outer_bounds = False

        # --- Visual State ---
        self.current_alpha = 1.0
        self.current_color_val = 0.5

        # --- Rotation Animation State ---
        self.is_rotating = False
        self.rot_full_angle = 45.0
        self.rot_target_angle = 22.5
        self.rot_dir = 1
        self.rot_accumulated = 0.0
        self.rot_speed_dps = 5.0        # Degrees to rotate per second
        self.rot_step_deg = 0.25        # Legacy default at ~60 FPS
        
        self.cam_radius = 2.0 

        # --- Setup Plotly Figure ---
        self.fig = go.Figure()

        # Trace 0: Base Points (The Grid)
        self.fig.add_trace(go.Scatter3d(x=[], y=[], z=[], mode='markers',
                                        marker=dict(size=3, color='#00FFFF', opacity=self.current_alpha),
                                        hoverinfo='none', showlegend=False))
        # Trace 1: History Line (Grey)
        self.fig.add_trace(go.Scatter3d(x=[], y=[], z=[], mode='lines',
                                        line=dict(color='#777777', width=2),
                                        hoverinfo='none', showlegend=False))
        # Trace 2: Active Tail Line (Red)
        self.fig.add_trace(go.Scatter3d(x=[], y=[], z=[], mode='lines',
                                        line=dict(color='red', width=5),
                                        hoverinfo='none', showlegend=False))
        # Trace 3: Head Point (Blue)
        self.fig.add_trace(go.Scatter3d(x=[], y=[], z=[], mode='markers',
                                        marker=dict(size=6, color='blue', symbol='circle'),
                                        hoverinfo='none', showlegend=False))
        # Trace 4: Inner Cylinder (Transparent Light Blue)
        self.fig.add_trace(go.Surface(x=[[0, 1], [0, 1]], y=[[0, 1], [0, 1]], z=[[0, 0], [1, 1]],
                                      colorscale=[[0, 'lightblue'], [1, 'lightblue']],
                                      showscale=False, hoverinfo='skip', opacity=0.3, visible=False,
                                      contours=dict(x=dict(highlight=False), y=dict(highlight=False), z=dict(highlight=False))))
        # Trace 5: Inner Cylinder Top Cap
        self.fig.add_trace(go.Surface(x=[[0, 1], [0, 1]], y=[[0, 1], [0, 1]], z=[[1, 1], [1, 1]],
                                      colorscale=[[0, 'lightblue'], [1, 'lightblue']],
                                      showscale=False, hoverinfo='skip', opacity=0.3, visible=False,
                                      contours=dict(x=dict(highlight=False), y=dict(highlight=False), z=dict(highlight=False))))
        # Trace 6: Inner Cylinder Bottom Cap
        self.fig.add_trace(go.Surface(x=[[0, 1], [0, 1]], y=[[0, 1], [0, 1]], z=[[0, 0], [0, 0]],
                                      colorscale=[[0, 'lightblue'], [1, 'lightblue']],
                                      showscale=False, hoverinfo='skip', opacity=0.3, visible=False,
                                      contours=dict(x=dict(highlight=False), y=dict(highlight=False), z=dict(highlight=False))))
        # Trace 7: Outer Cylinder Wireframe
        self.fig.add_trace(go.Scatter3d(x=[], y=[], z=[], mode='lines',
                                        line=dict(color='gray', width=2),
                                        hoverinfo='skip', showlegend=False))

        # Configure layout for a clean, scientific 3D look
        self.ui_revision = 0
        self.fig.update_layout(
            margin=dict(l=0, r=0, b=0, t=0),
            hovermode=False,
            scene=dict(
                xaxis_title='X (m)', yaxis_title='Y (m)', zaxis_title='Z (m)',
                xaxis=dict(showspikes=False),
                yaxis=dict(showspikes=False),
                zaxis=dict(showspikes=False),
                camera=dict(eye=dict(x=1.5, y=1.5, z=1.0))
            ),
            uirevision=self.ui_revision
        )

        # --- Setup NiceGUI UI ---
        with ui.element('div').classes('relative w-[90%] h-[600px] border rounded bg-white'):
            # Mount the Plotly figure
            self.plot = ui.plotly(self.fig).classes('w-full h-full')

            # Coordinate Readout Text
            self.txt_readout = ui.html("").classes(
                'absolute bottom-4 left-4 bg-white/60 p-3 rounded shadow border font-mono text-sm whitespace-pre pointer-events-none'
            )
            # Bounds Readout Text
            self.txt_bounds = ui.html("").classes(
                'absolute top-4 left-4 bg-white/60 p-3 rounded shadow border font-mono text-sm whitespace-pre pointer-events-none'
            )

        self.N = 0 
        self.x = self.y = self.z = self.phi_arr = np.array([]) 
        self._has_loaded_data = False

        if input_data is not None:
            self.load_data(input_data)

        # --- Internal animation loops ---
        self.anim = ui.timer(self.timer_interval_ms / 1000.0, self._on_frame)

    def load_data(self, input_data):
        if isinstance(input_data, pd.DataFrame):
            df = input_data
        elif isinstance(input_data, dict):
            df = pd.DataFrame(input_data)
        elif isinstance(input_data, str) and input_data.lower().endswith('.csv'):
            df = pd.read_csv(input_data)
        else:
            raise TypeError("Engine load_data expects a pandas DataFrame, dict, or CSV file path.")

        self.phi_arr = df["phi_deg"].to_numpy()
        self.N = len(self.phi_arr)
        self.r_m = df["r_xy_mm"].to_numpy() / 1000.0
        self.z_m = df["z_mm"].to_numpy() / 1000.0
        self.phi_rad = np.radians(self.phi_arr)
        
        self.x = self.r_m * np.cos(self.phi_rad)
        self.y = self.r_m * np.sin(self.phi_rad)
        self.z = self.z_m

        # Update the base points trace
        hex_color = _hsv_slider_to_hex(self.current_color_val)
        self.fig.data[0].x = self.x
        self.fig.data[0].y = self.y
        self.fig.data[0].z = self.z
        self.fig.data[0].marker.color = hex_color

        # --- Update Inner Cylinder ---
        self.has_inner_bounds = False
        self.has_outer_bounds = False
        settings_dict = {}
        r_int, h_int, r_ext, h_ext = 0.0, 0.0, 0.0, 0.0
        
        if "gen_settings" in df.columns:
            settings = df["gen_settings"].dropna().tolist()
            for s in settings:
                if isinstance(s, str) and "=" in s:
                    parts = s.split("=", 1)
                    settings_dict[parts[0].strip()] = parts[1].strip()
            
            r_int = float(settings_dict.get("cyl_radius_internal", settings_dict.get("cyl_radius_mm", 0)))
            if "cyl_radius_mm" in settings_dict and "cyl_radius_internal" not in settings_dict:
                r_int /= 1000.0
                
            h_int = float(settings_dict.get("cyl_height_internal", settings_dict.get("cyl_height_mm", 0)))
            if "cyl_height_mm" in settings_dict and "cyl_height_internal" not in settings_dict:
                h_int /= 1000.0
                
            r_ext_val = settings_dict.get("cyl_radius_external", None)
            if r_ext_val is None:
                r_ext = r_int + float(settings_dict.get("wall_thickness_mm", 0)) / 1000.0
            else:
                r_ext = float(r_ext_val)
                
            h_ext_val = settings_dict.get("cyl_height_external", None)
            if h_ext_val is None:
                h_ext = h_int + 2.0 * float(settings_dict.get("wall_thickness_mm", 0)) / 1000.0
            else:
                h_ext = float(h_ext_val)
            
            if r_int > 0 and h_int > 0 and len(self.z_m) > 0:
                self.has_inner_bounds = True
                # Determine precise center from generator settings to handle waypoints and cutoffs
                z_offset_str = settings_dict.get("z_offset_mm", "None")
                if z_offset_str != "None":
                    z_center = float(z_offset_str) / 1000.0
                elif settings_dict.get("z_midpoint_zero", "False") == "True":
                    z_center = 0.0
                else:
                    z_center = float(settings_dict.get("cyl_height_external", h_int)) / 2.0

                theta = np.linspace(0, 2*np.pi, 50)
                z_vals = np.array([z_center - h_int/2, z_center + h_int/2])
                theta_grid, z_grid = np.meshgrid(theta, z_vals)
                
                self.fig.data[4].x = r_int * np.cos(theta_grid)
                self.fig.data[4].y = r_int * np.sin(theta_grid)
                self.fig.data[4].z = z_grid
                self.fig.data[4].visible = self.show_bounds

                # Caps
                r_vals = np.array([0, r_int])
                theta_grid_caps, r_grid_caps = np.meshgrid(theta, r_vals)
                x_caps = r_grid_caps * np.cos(theta_grid_caps)
                y_caps = r_grid_caps * np.sin(theta_grid_caps)
                
                # Top cap
                self.fig.data[5].x = x_caps
                self.fig.data[5].y = y_caps
                self.fig.data[5].z = np.full_like(x_caps, z_center + h_int/2)
                self.fig.data[5].visible = self.show_bounds
                
                # Bottom cap
                self.fig.data[6].x = x_caps
                self.fig.data[6].y = y_caps
                self.fig.data[6].z = np.full_like(x_caps, z_center - h_int/2)
                self.fig.data[6].visible = self.show_bounds

                if r_ext > 0 and h_ext > 0:
                    self.has_outer_bounds = True
                    theta_line = np.linspace(0, 2*np.pi, 60)
                    x_rim = r_ext * np.cos(theta_line)
                    y_rim = r_ext * np.sin(theta_line)
                    
                    z_top_ext = np.full_like(x_rim, z_center + h_ext/2)
                    z_bot_ext = np.full_like(x_rim, z_center - h_ext/2)
                    
                    x_wf = list(x_rim) + [None] + list(x_rim) + [None]
                    y_wf = list(y_rim) + [None] + list(y_rim) + [None]
                    z_wf = list(z_top_ext) + [None] + list(z_bot_ext) + [None]
                    
                    for angle in np.linspace(0, 2*np.pi, 8, endpoint=False):
                        x_wf.extend([r_ext * np.cos(angle), r_ext * np.cos(angle), None])
                        y_wf.extend([r_ext * np.sin(angle), r_ext * np.sin(angle), None])
                        z_wf.extend([z_center - h_ext/2, z_center + h_ext/2, None])
                        
                    self.fig.data[7].x = x_wf
                    self.fig.data[7].y = y_wf
                    self.fig.data[7].z = z_wf
                    self.fig.data[7].visible = self.show_bounds
                else:
                    self.fig.data[7].visible = False
            else:
                self.fig.data[4].visible = False
                self.fig.data[5].visible = False
                self.fig.data[6].visible = False
                self.fig.data[7].visible = False
        else:
            self.fig.data[4].visible = False
            self.fig.data[5].visible = False
            self.fig.data[6].visible = False
            self.fig.data[7].visible = False
            
        # --- Update Extents & Bounds Readout ---
        if self.N > 0:
            r_min_val = float(settings_dict.get("r_min_mm", np.min(self.r_m)*1000))
            r_max_val = float(settings_dict.get("r_max_mm", np.max(self.r_m)*1000))
            p_min_val = float(settings_dict.get("p_min_deg", np.min(self.phi_arr)))
            p_max_val = float(settings_dict.get("p_max_deg", np.max(self.phi_arr)))
            z_min_val = float(settings_dict.get("z_min_mm", np.min(self.z_m)*1000))
            z_max_val = float(settings_dict.get("z_max_mm", np.max(self.z_m)*1000))

            self.txt_bounds.content = (
                '<div class="flex gap-8">'
                '<div><b>Coordinate Extents</b>\n'
                f'R: {r_min_val:>6.1f} to {r_max_val:<6.1f}\n'
                f'P: {p_min_val:>6.1f} to {p_max_val:<6.1f}\n'
                f'Z: {z_min_val:>6.1f} to {z_max_val:<6.1f}</div>'
                '<div><b>Cylinder Bounds</b>\n'
                f'Inner: R{r_int*1000:<5.1f} H{h_int*1000:<5.1f}\n'
                f'Outer: R{r_ext*1000:<5.1f} H{h_ext*1000:<5.1f}</div>'
                '</div>'
            )
            self.txt_bounds.set_visibility(self.show_readout)
        
        if self.curr_idx >= self.N:
            self.curr_idx = max(0, self.N - 1)
            self.exact_idx = float(self.curr_idx)

        self._set_axes_equal()

        if self.N > 0:
            self.fig.data[3].x = [float(self.x[self.curr_idx])]
            self.fig.data[3].y = [float(self.y[self.curr_idx])]
            self.fig.data[3].z = [float(self.z[self.curr_idx])]

        is_reload = self._has_loaded_data
        if is_reload:
            self._replace_browser_figure_preserving_camera()
        else:
            self.plot.update()
        self._has_loaded_data = True
        
        if self.N > 0:
            if not is_reload:
                self._init_browser_loop()
            self._update_readout()

    def _set_axes_equal(self):
        if self.N == 0: return
        self.fig.update_layout(scene=dict(aspectmode='data'))

    def _replace_browser_figure_preserving_camera(self):
        if not hasattr(self, 'plot') or not self.plot.id:
            return
        figure_json = self.fig.to_json()
        state = {
            'curr_idx': self.curr_idx,
            'displayed_idx': self.curr_idx,
            'N': self.N,
            'full_x': self.x.tolist(),
            'full_y': self.y.tolist(),
            'full_z': self.z.tolist(),
            'rot_speed_dps': self.rot_speed_dps,
            'forceRedraw': True,
        }
        js = f"""
        var el = getElement({self.plot.id});
        if (el && el.$el && window.Plotly) {{
            var plotDiv = el.$el;
            var currentCamera = (
                plotDiv.layout &&
                plotDiv.layout.scene &&
                plotDiv.layout.scene.camera
            ) ? JSON.parse(JSON.stringify(plotDiv.layout.scene.camera)) : null;
            var figure = {figure_json};
            if (currentCamera) {{
                figure.layout = figure.layout || {{}};
                figure.layout.scene = figure.layout.scene || {{}};
                figure.layout.scene.camera = currentCamera;
            }}
            window.Plotly.react(
                plotDiv,
                figure.data,
                figure.layout,
                figure.config || {{responsive: true}}
            ).then(() => {{
                if (currentCamera) {{
                    window.Plotly.relayout(plotDiv, {{'scene.camera': currentCamera}});
                }}
                if (plotDiv._viewerState) {{
                    Object.assign(plotDiv._viewerState, {json.dumps(state)});
                }}
            }});
        }}
        """
        self._safe_run_javascript(js)

    # --- External Control API ---

    def set_current_index(self, idx):
        if self.is_playing: return 
        idx = max(0, min(int(idx), self.N - 1))
        if idx != self.curr_idx:
            self.curr_idx = idx
            self.exact_idx = float(idx)
            self._push_state()
            self._update_readout()

    def play(self): 
        self.is_playing = True
        
    def pause(self): 
        self.is_playing = False
        
    def rewind(self): 
        self.pause()
        self.curr_idx = 0
        self.exact_idx = 0.0
        self._push_state()
        self._update_readout()
    
    def step_fwd(self):
        self.pause()
        if self.curr_idx < self.N - 1: 
            self.curr_idx += 1
            self._push_state()
            self._update_readout()

    def step_back(self):
        self.pause()
        if self.curr_idx > 0: 
            self.curr_idx -= 1
            self._push_state()
            self._update_readout()

    def set_speed(self, ppm):
        if ppm > 0: self.ppm = ppm

    def set_tail_length(self, val):
        self.tail_length = int(val)
        self._push_state()

    def set_history_mode(self, enabled: bool):
        self.use_history_fading = enabled
        self._push_state()

    def set_rotation_speed(self, deg_per_sec):
        try:
            speed = float(deg_per_sec)
        except (TypeError, ValueError):
            return
        if speed <= 0:
            return
        self.rot_speed_dps = speed
        if hasattr(self, 'plot') and self.plot.id:
            js = f"""
            var el = getElement({self.plot.id});
            if (el && el.$el && el.$el._viewerState) {{
                el.$el._viewerState.rot_speed_dps = {json.dumps(self.rot_speed_dps)};
            }}
            """
            self._safe_run_javascript(js)

    def set_ortho(self, enabled: bool):
        self.use_ortho = enabled
        proj_type = 'orthographic' if self.use_ortho else 'perspective'
        self.fig.layout.scene.camera.projection.type = proj_type
        
        cam_update = {"scene.camera.projection.type": proj_type}
        js = f"var el = getElement({self.plot.id}); if (el && el.$el && window.Plotly) window.Plotly.relayout(el.$el, {json.dumps(cam_update)});"
        self._safe_run_javascript(js)

    def set_view(self, elev, azim):
        el_rad = np.radians(elev)
        az_rad = np.radians(azim)
        
        r = 2.0 
        x = r * np.cos(el_rad) * np.sin(az_rad)
        y = r * np.cos(el_rad) * np.cos(az_rad)
        z = r * np.sin(el_rad)
        
        self.fig.layout.scene.camera.eye = dict(x=x, y=y, z=z)
        cam_update = {"scene.camera.eye": {"x": x, "y": y, "z": z}}
        js = f"var el = getElement({self.plot.id}); if (el && el.$el && window.Plotly) window.Plotly.relayout(el.$el, {json.dumps(cam_update)});"
        self._safe_run_javascript(js)

    def set_alpha(self, val):
        self.current_alpha = float(val)
        self.fig.data[0].marker.opacity = self.current_alpha
        js = f"var el = getElement({self.plot.id}); if (el && el.$el && window.Plotly) window.Plotly.restyle(el.$el, {json.dumps({'marker.opacity': self.current_alpha})}, [0]);"
        self._safe_run_javascript(js)

    def set_color(self, val):
        self.current_color_val = float(val)
        hex_color = _hsv_slider_to_hex(self.current_color_val)
        self.fig.data[0].marker.color = hex_color
        js = f"var el = getElement({self.plot.id}); if (el && el.$el && window.Plotly) window.Plotly.restyle(el.$el, {json.dumps({'marker.color': hex_color})}, [0]);"
        self._safe_run_javascript(js)

    def set_bounds_visibility(self, visible: bool):
        self.show_bounds = visible
        v456 = self.show_bounds if getattr(self, 'has_inner_bounds', False) else False
        v7 = self.show_bounds if getattr(self, 'has_outer_bounds', False) else False
        
        self.fig.data[4].visible = v456
        self.fig.data[5].visible = v456
        self.fig.data[6].visible = v456
        self.fig.data[7].visible = v7
        
        js = f"var el = getElement({self.plot.id}); if (el && el.$el && window.Plotly) window.Plotly.restyle(el.$el, {{visible: [{str(v456).lower()}, {str(v456).lower()}, {str(v456).lower()}, {str(v7).lower()}]}}, [4, 5, 6, 7]);"
        self._safe_run_javascript(js)

    def toggle_readout(self, force_state=None):
        self.show_readout = not self.show_readout if force_state is None else force_state
        self.txt_readout.set_visibility(self.show_readout)
        self.txt_bounds.set_visibility(self.show_readout)

    def start_rotation(self, angle=45.0):
        self.is_rotating = True
        self.rot_full_angle = angle
        self.rot_target_angle = angle / 2.0
        self.rot_dir = 1
        
        state = {
            'rotating': True,
            'rot_full_angle': self.rot_full_angle,
            'rot_target_angle': self.rot_target_angle,
            'rot_dir': self.rot_dir,
            'rot_accumulated': 0.0,
            'rot_speed_dps': self.rot_speed_dps
        }
        if hasattr(self, 'plot') and self.plot.id:
            js = f"""
            var el = getElement({self.plot.id});
            if (el && el.$el && el.$el._viewerState) {{
                Object.assign(el.$el._viewerState, {json.dumps(state)});
            }}
            """
            self._safe_run_javascript(js)

    def stop_rotation(self):
        self.is_rotating = False
        self._push_state()

    # --- Render Logic ---
    def _on_frame(self):
        if self.is_playing and self.N > 0:
            step = (self.ppm / 60.0) * (self.timer_interval_ms / 1000.0)
            self.exact_idx += step
            new_idx = int(self.exact_idx)
            
            if new_idx > self.curr_idx:
                if new_idx >= self.N:
                    self.curr_idx = self.N - 1
                    self.pause()
                else:
                    self.curr_idx = new_idx
                self._push_state()
                self._update_readout()

    def _push_state(self):
        if not hasattr(self, 'plot') or not self.plot.id: return
        state = {
            'curr_idx': self.curr_idx,
            'rotating': self.is_rotating,
            'rot_speed_dps': self.rot_speed_dps,
            'tail_length': self.tail_length,
            'use_history_fading': self.use_history_fading
        }
        js = f"""
        var el = getElement({self.plot.id});
        if (el && el.$el && el.$el._viewerState) {{
            Object.assign(el.$el._viewerState, {json.dumps(state)});
            el.$el._viewerState.forceRedraw = true;
        }}
        """
        self._safe_run_javascript(js)

    def _update_readout(self):
        if not self.show_readout or self.N == 0:
            return
            
        i = self.curr_idx
        lines = []
        for offset in range(-1, 3):
            idx = i + offset
            if 0 <= idx < self.N:
                prefix = "->" if offset == 0 else "  " 
                lines.append(f"{prefix}{idx:4d} | <b>R</b>{self.r_m[idx]*1000:6.1f}  <b>P</b>{self.phi_arr[idx]:6.1f}  <b>Z</b>{self.z[idx]*1000:6.1f}")
            else:
                lines.append("       ---") 
        self.txt_readout.content = "\n".join(lines)

    def _init_browser_loop(self):
        if not hasattr(self, 'plot') or not self.plot.id: return
        js = f"""
        function initViewerLoop(attempt = 0) {{
            var el = getElement({self.plot.id});
            if (!(el && el.$el && window.Plotly && el.$el.data && el.$el.data.length >= 4)) {{
                // On slower clients (especially mobile), the NiceGUI wrapper can exist
                // before Plotly has finished mounting its graph. Retry instead of making
                // a single best-effort attempt and silently giving up.
                if (attempt < 50) setTimeout(() => initViewerLoop(attempt + 1), 100);
                return;
            }}

            if (el && el.$el && window.Plotly) {{
                var plotDiv = el.$el;

                function ensureViewerInteractionHandlers() {{
                    function markCameraInteraction() {{
                        if (!plotDiv._viewerState) return;
                        plotDiv._viewerState.isUserDragging = true;
                        plotDiv._viewerState.lastCameraInteractionMs = performance.now();
                    }}

                    function releaseCameraInteraction(delayMs) {{
                        setTimeout(() => {{
                            if (plotDiv._viewerState) {{
                                plotDiv._viewerState.isUserDragging = false;
                                plotDiv._viewerState.lastCameraInteractionMs = performance.now();
                            }}
                        }}, delayMs);
                    }}

                    if (plotDiv._viewerInteractionHandlersReady) {{
                        return;
                    }}
                    plotDiv._viewerInteractionHandlersReady = true;

                    plotDiv.addEventListener('mousedown', markCameraInteraction, true);
                    window.addEventListener('mouseup', () => releaseCameraInteraction(150), true);
                    plotDiv.addEventListener('touchstart', markCameraInteraction, true);
                    window.addEventListener('touchend', () => releaseCameraInteraction(150), true);
                    if (window.PointerEvent) {{
                        plotDiv.addEventListener('pointerdown', markCameraInteraction, true);
                        window.addEventListener('pointerup', () => releaseCameraInteraction(150), true);
                    }}
                    plotDiv.addEventListener('wheel', () => {{
                        markCameraInteraction();
                        if (plotDiv.wheelTimeout) clearTimeout(plotDiv.wheelTimeout);
                        plotDiv.wheelTimeout = setTimeout(() => releaseCameraInteraction(0), 250);
                    }}, true);
                }}

                if (!plotDiv._viewerState) {{
                    plotDiv._viewerState = {{
                        curr_idx: {self.curr_idx},
                        displayed_idx: {self.curr_idx},
                        rotating: {str(self.is_rotating).lower()},
                        rot_speed_dps: {self.rot_speed_dps},
                        rot_step_deg: {self.rot_step_deg},
                        rot_dir: {self.rot_dir},
                        rot_accumulated: {self.rot_accumulated},
                        rot_target_angle: {self.rot_target_angle},
                        rot_full_angle: {self.rot_full_angle},
                        tail_length: {self.tail_length},
                        use_history_fading: {str(self.use_history_fading).lower()},
                        N: {self.N},
                        isUserDragging: false,
                        forceRedraw: false,
                        pendingRedraw: false,
                        lastRenderMs: 0,
                        lastCameraInteractionMs: 0,
                        lastTraceRedrawMs: 0,
                        lastTraceIdx: -1,
                        traceRedrawIntervalMs: 100,
                        full_x: {json.dumps(self.x.tolist())},
                        full_y: {json.dumps(self.y.tolist())},
                        full_z: {json.dumps(self.z.tolist())}
                    }};
                    ensureViewerInteractionHandlers();

                    function rebuildViewerTraces(i) {{
                        var st = plotDiv._viewerState;
                        var s_act = st.use_history_fading ? Math.max(0, i - st.tail_length) : 0;
                        if (plotDiv.data && plotDiv.data.length >= 4) {{
                            plotDiv.data[3].x = [st.full_x[i]];
                            plotDiv.data[3].y = [st.full_y[i]];
                            plotDiv.data[3].z = [st.full_z[i]];
                            
                            plotDiv.data[2].x = st.full_x.slice(s_act, i + 1);
                            plotDiv.data[2].y = st.full_y.slice(s_act, i + 1);
                            plotDiv.data[2].z = st.full_z.slice(s_act, i + 1);
                            
                            if (st.use_history_fading && s_act > 0) {{
                                plotDiv.data[1].x = st.full_x.slice(0, s_act);
                                plotDiv.data[1].y = st.full_y.slice(0, s_act);
                                plotDiv.data[1].z = st.full_z.slice(0, s_act);
                            }} else {{
                                plotDiv.data[1].x = [];
                                plotDiv.data[1].y = [];
                                plotDiv.data[1].z = [];
                            }}
                        }}
                    }}

                    function applyViewerUpdate(cameraChanged) {{
                        window.Plotly.restyle(plotDiv, {{
                            x: [plotDiv.data[1].x, plotDiv.data[2].x, plotDiv.data[3].x],
                            y: [plotDiv.data[1].y, plotDiv.data[2].y, plotDiv.data[3].y],
                            z: [plotDiv.data[1].z, plotDiv.data[2].z, plotDiv.data[3].z]
                        }}, [1, 2, 3]);
                        if (cameraChanged) {{
                            window.Plotly.relayout(plotDiv, {{
                                'scene.camera.eye': plotDiv.layout.scene.camera.eye
                            }});
                        }}
                    }}

                    function renderLoop() {{
                        requestAnimationFrame(renderLoop);
                        var st = plotDiv._viewerState;
                        if (st.N === 0) return;
                        
                        var nowMs = performance.now();
                        var frameDeltaMs = st.lastRenderMs > 0 ? nowMs - st.lastRenderMs : (1000.0 / 60.0);
                        st.lastRenderMs = nowMs;
                        frameDeltaMs = Math.max(0, Math.min(frameDeltaMs, 100));
                        var cameraChanged = false;
                        var forceRedraw = false;
                        var needsRedraw = false;
                        var userCameraBusy = (
                            st.isUserDragging ||
                            (
                                st.lastCameraInteractionMs &&
                                (nowMs - st.lastCameraInteractionMs) < 300
                            )
                        );
                        
                        if (Math.abs(st.curr_idx - st.displayed_idx) > 0.01) {{
                            st.displayed_idx += (st.curr_idx - st.displayed_idx) * 0.3;
                            needsRedraw = true;
                        }} else {{
                            if (st.displayed_idx !== st.curr_idx) {{
                                st.displayed_idx = st.curr_idx;
                                needsRedraw = true;
                            }}
                        }}
                        
                        if (st.forceRedraw) {{
                            needsRedraw = true;
                            forceRedraw = true;
                            st.forceRedraw = false;
                        }}
                        
                        var i = Math.floor(st.displayed_idx);
                        i = Math.max(0, Math.min(i, st.N - 1));

                        var canRedrawTraces = (
                            !st.rotating ||
                            forceRedraw ||
                            (
                                i !== st.lastTraceIdx &&
                                (nowMs - st.lastTraceRedrawMs) >= st.traceRedrawIntervalMs
                            )
                        );
                        
                        if (st.rotating && !userCameraBusy && plotDiv.layout && plotDiv.layout.scene && plotDiv.layout.scene.camera) {{
                            var rotStepDeg = st.rot_speed_dps !== undefined ? st.rot_speed_dps * frameDeltaMs / 1000.0 : st.rot_step_deg;
                            st.rot_accumulated += rotStepDeg;
                            if (st.rot_accumulated >= st.rot_target_angle) {{
                                st.rot_dir *= -1;
                                st.rot_accumulated = 0.0;
                                st.rot_target_angle = st.rot_full_angle;
                            }}
                            var eye = plotDiv.layout.scene.camera.eye;
                            if (eye && eye.x !== undefined) {{
                                var rad = rotStepDeg * st.rot_dir * Math.PI / 180.0;
                                var new_x = eye.x * Math.cos(rad) - eye.y * Math.sin(rad);
                                var new_y = eye.x * Math.sin(rad) + eye.y * Math.cos(rad);
                                eye.x = new_x;
                                eye.y = new_y;
                                cameraChanged = true;
                            }}
                        }}
                        
                        if (needsRedraw && canRedrawTraces) {{
                            st.lastTraceRedrawMs = nowMs;
                            st.lastTraceIdx = i;
                            rebuildViewerTraces(i);
                            if (userCameraBusy) {{
                                // Let Plotly own the camera interaction while the user is dragging.
                                // Repeated redraws during playback interrupt the drag gesture.
                                st.pendingRedraw = true;
                            }} else {{
                                applyViewerUpdate(cameraChanged);
                                st.pendingRedraw = false;
                            }}
                        }} else if (needsRedraw) {{
                            st.pendingRedraw = true;

                            if (cameraChanged && !userCameraBusy) {{
                                window.Plotly.relayout(plotDiv, {{
                                    'scene.camera.eye': plotDiv.layout.scene.camera.eye
                                }});
                            }}
                        }} else if (cameraChanged && !userCameraBusy) {{
                            window.Plotly.relayout(plotDiv, {{
                                'scene.camera.eye': plotDiv.layout.scene.camera.eye
                            }});
                        }} else if (!userCameraBusy && st.pendingRedraw) {{
                            // Playback may have advanced while the camera was being moved.
                            // Apply the latest frame once the interaction finishes.
                            rebuildViewerTraces(i);
                            applyViewerUpdate(false);
                            st.lastTraceRedrawMs = nowMs;
                            st.lastTraceIdx = i;
                            st.pendingRedraw = false;
                        }}
                    }}
                    renderLoop();
                }} else {{
                    plotDiv._viewerState.N = {self.N};
                    plotDiv._viewerState.full_x = {json.dumps(self.x.tolist())};
                    plotDiv._viewerState.full_y = {json.dumps(self.y.tolist())};
                    plotDiv._viewerState.full_z = {json.dumps(self.z.tolist())};
                    plotDiv._viewerState.curr_idx = {self.curr_idx};
                    plotDiv._viewerState.displayed_idx = {self.curr_idx};
                    plotDiv._viewerState.rot_speed_dps = {self.rot_speed_dps};
                    if (plotDiv._viewerState.lastRenderMs === undefined) plotDiv._viewerState.lastRenderMs = 0;
                    if (plotDiv._viewerState.lastCameraInteractionMs === undefined) plotDiv._viewerState.lastCameraInteractionMs = 0;
                    if (plotDiv._viewerState.lastTraceRedrawMs === undefined) plotDiv._viewerState.lastTraceRedrawMs = 0;
                    if (plotDiv._viewerState.lastTraceIdx === undefined) plotDiv._viewerState.lastTraceIdx = -1;
                    if (plotDiv._viewerState.traceRedrawIntervalMs === undefined) plotDiv._viewerState.traceRedrawIntervalMs = 100;
                    ensureViewerInteractionHandlers();
                    plotDiv._viewerState.forceRedraw = true;
                }}
            }}
        }}
        setTimeout(() => initViewerLoop(), 50);
        """
        self._safe_run_javascript(js)

    def _safe_run_javascript(self, js: str):
        """Safely executes Javascript, deferring it if the async event loop hasn't started yet."""
        try:
            import asyncio
            asyncio.get_running_loop()
            ui.run_javascript(js)
        except RuntimeError:
            ui.timer(0.1, lambda: ui.run_javascript(js), once=True)
