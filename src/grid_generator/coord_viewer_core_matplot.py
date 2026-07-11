#!/usr/bin/env python3
"""
Pure Interactive 3D Replay Viewer Engine
-------------------------------------------------
This module provides a pure, GUI-framework agnostic rendering engine 
for visualizing 3D coordinate grids and paths. 

Features:
- 3D visualization of coordinate paths.
- Animated playback (scrubbing, play, pause, rewind).
- Auto-rotating camera views. Path history tail.
- Framework-agnostic design: This class only uses Matplotlib and
  exposes a clean API (methods like `play()`, `pause()`, `set_speed()`) 
  that allows any parent GUI to embed and control it easily.

Example Usage:
Please see `coord_viewer_util.py` for a complete example of how to wrap 
this engine inside a standalone Tkinter application.
"""

import numpy as np  # Used for high-performance mathematical operations and arrays
import matplotlib as mpl
import matplotlib.pyplot as plt  # Core plotting library to create figures and axes
import matplotlib.colors as mcolors  # Used to create custom color gradients
import matplotlib.animation as animation  # Used to create background loops for playback
import pandas as pd  # Used for handling tabular data (DataFrames)
import json  # Used for parsing settings embedded in columns
import ast
import colorsys


def _hsv_slider_to_hex(value):
    value = max(0.0, min(1.0, float(value)))
    if value <= 0.0:
        return '#000000'
    r, g, b = colorsys.hsv_to_rgb(value % 1.0, 1.0, 1.0)
    return f'#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}'

class CoordViewerEngine:
    """
    Pure rendering engine. Knows nothing about UI widgets or file paths.
    Expects input data as a pandas DataFrame, dict, or CSV path.
    """
    def __init__(self, input_data=None):
        # --- Playback State ---
        # These variables keep track of where we are in the animation
        self.curr_idx = 0  # The current discrete point index being displayed
        self.exact_idx = 0.0  # A floating-point index for smooth speed calculations
        self.is_playing = False  # Boolean flag to know if the animation is currently running
        self.ppm = 600.0  # Points Per Minute (playback speed)
        self.tail_length = 50  # How many previous points to show trailing behind the current point
        self.use_history_fading = False  # Toggle whether to show the entire past path faintly
        self.use_ortho = False  # Toggle between orthographic and perspective 3D projections
        self.timer_interval_ms = 50  # How often the animation loop updates (in milliseconds)
        self.show_readout = True  # Toggle the text box showing coordinates
        self._use_z_up_turntable_rotation()

        # --- Rotation Animation State ---
        # These variables handle the automated camera panning back and forth
        self.is_rotating = False  # Flag to know if the camera is currently panning
        self.rot_full_angle = 45.0  # The total angle sweep of the camera pan
        self.rot_target_angle = 22.5  # The half-way target to reverse direction
        self.rot_dir = 1  # Direction multiplier (1 for right, -1 for left)
        self.rot_accumulated = 0.0  # Tracks how far the camera has rotated in the current sweep
        self.rotation_speed_deg_per_sec = 5.0
        self.rotation_timer_interval_ms = 20
        self._is_camera_dragging = False
        self._pending_drag_redraw = False

        # --- Visual State ---
        self.current_alpha = 1.0
        self.current_color_val = 0.5
        self.default_point_color = _hsv_slider_to_hex(self.current_color_val)
        self.inner_cylinder_color = 'lightblue'
        self.inner_cylinder_alpha = 0.3

        # --- Setup Figure ---
        # Create the main Matplotlib figure window. figsize is in inches (width, height).
        self.fig = plt.figure(figsize=(10, 8))
        # Adjust the margins of the plot to make room for the text readout at the bottom
        self.fig.subplots_adjust(top=1.0, bottom=0.10, left=0.0, right=1.0)
        # Add a 3D axis to the figure
        self.ax = self.fig.add_subplot(111, projection='3d')
        # Set the labels for the 3 axes
        self.ax.set_xlabel("X (m)")
        self.ax.set_ylabel("Y (m)")
        self.ax.set_zlabel("Z (m)")
        # Set the initial camera viewing angle (elevation and azimuth)
        self._set_view_z_up(30, -45)

        # Avoid playback redraws fighting Matplotlib's own camera drag loop.
        self.fig.canvas.mpl_connect('button_press_event', self._on_mouse_press)
        self.fig.canvas.mpl_connect('button_release_event', self._on_mouse_release)

        # --- Custom Colormap ---
        # Creates a color gradient that starts at black and transitions through the HSV rainbow
        self.custom_cmap = mcolors.LinearSegmentedColormap.from_list(
            'black_hsv', 
            ['black'] + [plt.get_cmap('hsv')(i) for i in np.linspace(0, 1, 256)]
        )

        # --- Fast line plots ---
        # To make animations fast, we don't redraw the whole plot. Instead, we create empty 
        # line objects here (with [], [], []) and just update their data arrays later.
        self.base_pts, = self.ax.plot([], [], [], marker='o', linestyle='none', color=self.default_point_color, markersize=3, alpha=self.current_alpha)
        self.line_hist, = self.ax.plot([], [], [], c='#777777', alpha=0.3, linewidth=1.0)
        self.line_active, = self.ax.plot([], [], [], c='red', linewidth=2.0)
        self.head_pt, = self.ax.plot([], [], [], marker='o', c='blue', markersize=6)

        # --- Coordinate Readout Text ---
        # Place a text box anchored to the figure window (not the 3D space) at 2% X and 2% Y
        self.txt_readout = self.fig.text(
            0.02, 0.02, "",
            fontsize=9, family='monospace', verticalalignment='bottom',
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.7)
        )

        # Initialize default data properties as empty numpy arrays
        self.N = 0 
        self.x = self.y = self.z = self.phi_arr = np.array([]) 
        
        # --- Bounds Visuals ---
        self.show_bounds = True
        self.show_grid = True
        self._bound_artists = []
        self.inner_r = 0.0
        self.outer_r = 0.0
        self.inner_z_min = 0.0
        self.inner_z_max = 0.0
        self.outer_z_min = 0.0
        self.outer_z_max = 0.0

        # If data was passed when creating the engine, load it immediately
        if input_data is not None:
            self.load_data(input_data)

        # --- Internal animation loops ---
        # These are background timers that call a function repeatedly at set intervals.
        # `self._on_frame` handles the playback movement. `self._on_rotate_frame` handles camera panning.
        self.anim = animation.FuncAnimation(self.fig, self._on_frame, interval=self.timer_interval_ms, cache_frame_data=False)
        self.rot_anim = animation.FuncAnimation(self.fig, self._on_rotate_frame, interval=self.rotation_timer_interval_ms, cache_frame_data=False)

    def _on_mouse_press(self, event):
        if event.inaxes is self.ax:
            self._is_camera_dragging = True

    def _on_mouse_release(self, event):
        if self._is_camera_dragging:
            self._is_camera_dragging = False
            self._enforce_camera_constraints()
            if self._pending_drag_redraw:
                self._pending_drag_redraw = False
                self.fig.canvas.draw_idle()

    def _request_draw(self):
        if self._is_camera_dragging:
            self._pending_drag_redraw = True
        else:
            self.fig.canvas.draw_idle()

    def _enforce_camera_constraints(self):
        # Keep the azel turntable away from the singular straight-up/down view.
        needs_redraw = False
        if hasattr(self.ax, 'elev'):
            if self.ax.elev > 89.9:
                self.ax.elev = 89.9
                needs_redraw = True
            elif self.ax.elev < -89.9:
                self.ax.elev = -89.9
                needs_redraw = True
        if needs_redraw:
            self.fig.canvas.draw_idle()

    def _use_z_up_turntable_rotation(self):
        if "axes3d.mouserotationstyle" in mpl.rcParams:
            mpl.rcParams["axes3d.mouserotationstyle"] = "azel"

    def _set_view_z_up(self, elev, azim):
        try:
            self.ax.view_init(elev=elev, azim=azim, roll=0, vertical_axis="z")
        except TypeError:
            try:
                self.ax.view_init(elev=elev, azim=azim, roll=0)
            except TypeError:
                self.ax.view_init(elev=elev, azim=azim)

    def load_data(self, input_data):
        # Determine what type of data was passed in and standardize it into a Pandas DataFrame
        if isinstance(input_data, pd.DataFrame):
            df = input_data
        elif isinstance(input_data, dict):
            df = pd.DataFrame(input_data)
        elif isinstance(input_data, str) and input_data.lower().endswith('.csv'):
            df = pd.read_csv(input_data)
        else:
            raise TypeError("Engine load_data expects a pandas DataFrame, dict, or CSV file path.")

        # Extract specific columns into fast Numpy arrays
        self.phi_arr = df["phi_deg"].to_numpy()
        self.N = len(self.phi_arr)  # Total number of points
        self.r_m = df["r_xy_mm"].to_numpy() / 1000.0  # Convert mm to meters
        self.z_m = df["z_mm"].to_numpy() / 1000.0  # Convert mm to meters
        self.phi_rad = np.radians(self.phi_arr)  # Convert degrees to radians for math functions
        
        # Convert polar coordinates (radius, angle) into cartesian coordinates (X, Y)
        self.x = self.r_m * np.cos(self.phi_rad)
        self.y = self.r_m * np.sin(self.phi_rad)
        self.z = self.z_m

        # Update the base (background) points with the full dataset
        self.base_pts.set_data(self.x, self.y)
        self.base_pts.set_3d_properties(self.z)

        # Determine bounds from CSV columns or JSON gen_settings if possible
        gen_settings = {}
        if "gen_settings" in df.columns:
            # Scan rows for a valid gen_settings entry (in case row 0 is NaN)
            # The gen_settings column contains key=value pairs, one per row.
            # We need to iterate through the column and build a dictionary.
            for item in df["gen_settings"].dropna():
                item_str = str(item).strip()
                if not item_str or "=" not in item_str:
                    continue
                
                key, value = item_str.split("=", 1)
                key = key.strip()
                value = value.strip()

                try:
                    # ast.literal_eval is a safe way to evaluate Python literals
                    gen_settings[key] = ast.literal_eval(value)
                except (ValueError, SyntaxError, MemoryError, TypeError):
                    # If it's not a valid literal (e.g., just a string like 'Auto'), keep it as a string.
                    gen_settings[key] = value
        
        def get_val(key, default=None):
            val = None
            # Prioritize gen_settings as it's the most explicit source.
            if key in gen_settings:
                val = gen_settings[key]
            elif key in df.columns:
                val = df[key].iloc[0]
            
            if val is None:
                return default
            
            try:
                if isinstance(val, (int, float, str, bool)):
                    if pd.isna(val):
                        return default
                    if isinstance(val, str) and val.strip() == '':
                        return default
                return val
            except Exception:
                return val

        # Reset bounds to zero before attempting to load new ones.
        # This ensures no old values persist if the new CSV lacks them.
        self.inner_r = 0.0
        self.outer_r = 0.0
        self.inner_z_min = 0.0
        self.inner_z_max = 0.0
        self.outer_z_min = 0.0
        self.outer_z_max = 0.0

        try:
            z_offset = float(get_val("z_offset_mm", 0.0)) / 1000.0

            inner_r_val = get_val("cyl_radius_internal")
            if inner_r_val is not None:
                self.inner_r = float(inner_r_val)

            outer_r_val = get_val("cyl_radius_external")
            if outer_r_val is not None:
                self.outer_r = float(outer_r_val)

            inner_h_val = get_val("cyl_height_internal")
            if inner_h_val is not None:
                h_int = float(inner_h_val)
                self.inner_z_min = z_offset - (h_int / 2.0)
                self.inner_z_max = z_offset + (h_int / 2.0)

            outer_h_val = get_val("cyl_height_external")
            if outer_h_val is not None:
                h_ext = float(outer_h_val)
                self.outer_z_min = z_offset - (h_ext / 2.0)
                self.outer_z_max = z_offset + (h_ext / 2.0)
                
            if self.inner_r <= 0 and self.outer_r <= 0:
                print("DEBUG CoordViewer: No valid internal or external bounding radii found in data.")
        except Exception as e:
            print(f"Error parsing bounding cylinder data: {e}")

        # Ensure our playback index hasn't exceeded the length of the new dataset
        if self.curr_idx >= self.N:
            self.curr_idx = max(0, self.N - 1)
            self.exact_idx = float(self.curr_idx)

        # Adjust the 3D axis limits so the plot doesn't look stretched or squished
        self._set_axes_equal()
        self._draw_bounds()
        # Force the plot to redraw with the new data
        self.update_plot()

    def _set_axes_equal(self):
        # Matplotlib 3D axes don't naturally maintain a 1:1:1 aspect ratio.
        # This function calculates the largest spread of data and forces all axes to use that range.
        if self.N == 0: return
        # Find the maximum range across all 3 dimensions
        max_range = np.array([
            self.x.max() - self.x.min(),
            self.y.max() - self.y.min(),
            self.z.max() - self.z.min()
        ]).max() / 2.0
        
        # Find the center point of the data
        mid_x = (self.x.max() + self.x.min()) * 0.5
        mid_y = (self.y.max() + self.y.min()) * 0.5
        mid_z = (self.z.max() + self.z.min()) * 0.5
        
        # Apply the limits so the bounding box is a perfect cube
        self.ax.set_xlim(mid_x - max_range, mid_x + max_range)
        self.ax.set_ylim(mid_y - max_range, mid_y + max_range)
        self.ax.set_zlim(mid_z - max_range, mid_z + max_range)

    def _draw_bounds(self):
        # Clear existing bounds
        if hasattr(self, '_bound_artists'):
            for artist in self._bound_artists:
                try:
                    artist.remove()
                except Exception:
                    pass
        self._bound_artists = []

        if self.N == 0:
            return

        theta = np.linspace(0, 2 * np.pi, 48)

        def add_inner_cylinder_surface(r, min_z, max_z):
            z_vals = np.array([min_z, max_z])
            theta_grid, z_grid = np.meshgrid(theta, z_vals)
            x_side = r * np.cos(theta_grid)
            y_side = r * np.sin(theta_grid)
            surface_kwargs = dict(
                color=self.inner_cylinder_color,
                alpha=self.inner_cylinder_alpha,
                linewidth=0,
                antialiased=False,
                shade=False,
            )

            side = self.ax.plot_surface(x_side, y_side, z_grid, **surface_kwargs)
            self._bound_artists.append(side)

            r_vals = np.array([0.0, r])
            theta_caps, r_caps = np.meshgrid(theta, r_vals)
            x_caps = r_caps * np.cos(theta_caps)
            y_caps = r_caps * np.sin(theta_caps)

            top = self.ax.plot_surface(
                x_caps, y_caps, np.full_like(x_caps, max_z), **surface_kwargs
            )
            bottom = self.ax.plot_surface(
                x_caps, y_caps, np.full_like(x_caps, min_z), **surface_kwargs
            )
            self._bound_artists.extend([top, bottom])
        
        def add_circle(r, z, color, alpha, style='-'):
            x = r * np.cos(theta)
            y = r * np.sin(theta)
            z_arr = np.full_like(x, z)
            line, = self.ax.plot(x, y, z_arr, color=color, alpha=alpha, linestyle=style)
            self._bound_artists.append(line)
            
        def add_struts(r, min_z, max_z, num_struts, color, alpha, style='-'):
            strut_theta = np.linspace(0, 2 * np.pi, num_struts, endpoint=False)
            x = r * np.cos(strut_theta)
            y = r * np.sin(strut_theta)
            for i in range(num_struts):
                line, = self.ax.plot([x[i], x[i]], [y[i], y[i]], [min_z, max_z], color=color, alpha=alpha, linestyle=style)
                self._bound_artists.append(line)

        # Draw inner cylinder as a translucent solid, matching the Plotly viewer.
        if self.inner_r > 0:
            add_inner_cylinder_surface(self.inner_r, self.inner_z_min, self.inner_z_max)
            
        # Draw outer cylinder (gray)
        if self.outer_r > 0:
            add_circle(self.outer_r, self.outer_z_min, 'gray', 0.3, '--')
            add_circle(self.outer_r, self.outer_z_max, 'gray', 0.3, '--')
            add_struts(self.outer_r, self.outer_z_min, self.outer_z_max, 12, 'gray', 0.2, '--')

        self.set_bounds_visibility(self.show_bounds)

    # --- External Control API ---
    # These methods are designed to be called by buttons and sliders in the parent GUI.

    def set_current_index(self, idx):
        # Jump to a specific point in the playback (e.g., from a slider)
        if self.is_playing: return  # Ignore manual scrub if currently playing
        idx = max(0, min(int(idx), self.N - 1))  # Clamp value to valid range
        if idx != self.curr_idx:
            self.curr_idx = idx
            self.exact_idx = float(idx)
            self.update_plot()

    def play(self): 
        # Start animation
        self.is_playing = True
        self.ax.set_title("Playing...")
        
    def pause(self): 
        # Stop animation
        self.is_playing = False
        self.ax.set_title("Paused")
        
    def rewind(self): 
        # Reset animation to the beginning
        self.pause()
        self.curr_idx = 0
        self.exact_idx = 0.0
        self.update_plot()
    
    def step_fwd(self):
        # Move forward exactly one point
        self.pause()
        if self.curr_idx < self.N - 1: 
            self.curr_idx += 1
            self.update_plot()

    def step_back(self):
        # Move backward exactly one point
        self.pause()
        if self.curr_idx > 0: 
            self.curr_idx -= 1
            self.update_plot()

    def set_speed(self, ppm):
        # Update playback speed (Points Per Minute)
        if ppm > 0: self.ppm = ppm

    def set_tail_length(self, val):
        # Update how long the red highlighted trail should be
        self.tail_length = int(val)
        if not self.is_playing: self.update_plot()

    def set_history_mode(self, enabled: bool):
        # Toggle the grey faded history line
        self.use_history_fading = enabled
        if not self.is_playing: self.update_plot()

    def set_ortho(self, enabled: bool):
        # Switch between orthographic (flat 3D) and perspective (depth 3D) rendering
        self.use_ortho = enabled
        self.ax.set_proj_type('ortho' if self.use_ortho else 'persp')
        self._request_draw()

    def set_view(self, elev, azim):
        # Snap the camera to a specific angle
        if abs(abs(float(elev)) - 90.0) <= 1e-6:
            elev = 89.0 if float(elev) >= 0.0 else -89.0
        self._set_view_z_up(elev, azim)
        self._request_draw()

    def set_alpha(self, val):
        # Change the transparency of the background points
        self.current_alpha = float(val)
        self.base_pts.set_alpha(self.current_alpha)
        self._request_draw()

    def set_color(self, val):
        # Pick a new color using the same HSV slider mapping as the Plotly viewer.
        self.current_color_val = float(val)
        self.base_pts.set_color(_hsv_slider_to_hex(self.current_color_val))
        self._request_draw()

    def set_rotation_speed(self, deg_per_sec):
        self.rotation_speed_deg_per_sec = max(0.1, float(deg_per_sec or 5.0))

    def set_bounds_visibility(self, visible: bool):
        self.show_bounds = visible
        if hasattr(self, '_bound_artists'):
            for artist in self._bound_artists:
                artist.set_visible(self.show_bounds)
        self._request_draw()

    def set_grid_visibility(self, visible: bool):
        self.show_grid = bool(visible)
        self.ax.grid(self.show_grid)
        self._request_draw()

    def toggle_readout(self, force_state=None):
        # Turn the bottom text box on or off
        self.show_readout = not self.show_readout if force_state is None else force_state
        self.txt_readout.set_visible(self.show_readout)
        
        # Adjust bottom margin to make room for the readout box if it's visible
        bottom_margin = 0.15 if self.show_readout else 0.05
        self.fig.subplots_adjust(bottom=bottom_margin)
        self._request_draw()

    def start_rotation(self, angle=45.0):
        # Initiate the automated camera panning
        self.is_rotating = True
        self.rot_full_angle = angle
        self.rot_target_angle = angle / 2.0
        self.rot_dir = 1
        self.rot_accumulated = 0.0

    def stop_rotation(self):
        # Stop the automated camera panning
        self.is_rotating = False

    # --- Render Logic ---
    def update_plot(self):
        # This is the core function that updates the visuals based on the current state.
        if self.N == 0: return

        i = self.curr_idx  # Current point index
        # Calculate where the red highlighted trail should start
        start_active = max(0, i - self.tail_length) if self.use_history_fading else 0

        # Update the blue "head" point to the current XYZ location
        self.head_pt.set_data([self.x[i]], [self.y[i]])
        self.head_pt.set_3d_properties([self.z[i]])
        
        # Update the red active line to stretch from start_active to the current point
        self.line_active.set_data(self.x[start_active:i+1], self.y[start_active:i+1])
        self.line_active.set_3d_properties(self.z[start_active:i+1])
        
        # If history mode is on, draw a gray line for all points *before* the active tail
        if self.use_history_fading and start_active > 0:
            self.line_hist.set_data(self.x[0:start_active], self.y[0:start_active])
            self.line_hist.set_3d_properties(self.z[0:start_active])
        else:
            # Otherwise, clear the gray history line
            self.line_hist.set_data([], [])
            self.line_hist.set_3d_properties([])

        # Update the text box showing the coordinate readout
        if self.show_readout:
            lines = []
            # Displaying 4 lines: 1 back, current, 2 forward
            for offset in range(-1, 3):
                idx = i + offset
                if 0 <= idx < self.N:
                    prefix = "->" if offset == 0 else "  "  # Add an arrow to the current row
                    # Format the text with proper spacing and decimal places
                    lines.append(f"{prefix} {idx:4d} | X{self.x[idx]*1000:6.1f} Y{self.y[idx]*1000:6.1f} Z{self.z[idx]*1000:6.1f} A{self.phi_arr[idx]:6.1f}")
                else:
                    lines.append("        ---")  # Placeholder if out of bounds
            self.txt_readout.set_text("\n".join(lines))

        # Tell matplotlib to redraw the figure when it has free time
        self._request_draw()

    def _on_frame(self, frame):
        # This method is called repeatedly by the animation timer.
        if self.is_playing and self.N > 0:
            # Calculate how many points we should advance based on our speed (ppm) and timer interval
            step = (self.ppm / 60.0) * (self.timer_interval_ms / 1000.0)
            self.exact_idx += step
            new_idx = int(self.exact_idx)
            
            # Only update the plot if we've accumulated enough steps to move to the next integer index
            if new_idx > self.curr_idx:
                if new_idx >= self.N:
                    # Reached the end of the data, stop playing
                    self.curr_idx = self.N - 1
                    self.pause()
                    self.ax.set_title("Replay Finished")
                else:
                    self.curr_idx = new_idx
                # Actually redraw the plot with the new index
                self.update_plot()
        # Animation functions must return an iterable of artists they modified
        return self.head_pt,

    def _on_rotate_frame(self, frame):
        # This method is called repeatedly by the secondary rotation animation timer.
        if not self.is_rotating or self._is_camera_dragging: return None
        
        step = self.rotation_speed_deg_per_sec * (self.rotation_timer_interval_ms / 1000.0)
        new_azim = self.ax.azim + (step * self.rot_dir)
        self._set_view_z_up(self.ax.elev, new_azim)
        self.rot_accumulated += step
        
        # Reverse direction if we've hit our target sweep angle
        if self.rot_accumulated >= self.rot_target_angle:
            self.rot_dir *= -1
            self.rot_accumulated = 0.0
            self.rot_target_angle = self.rot_full_angle
            
        self._request_draw()
        return None
