#!/usr/bin/env python3
"""
NiceGUI Demo Application for CoordViewerEngine (Plotly Edition)
-------------------------------------------------
Run this script directly to launch the web UI:
    python coord_viewer_demo.py
"""

import os
from pathlib import Path

try:
    from .grid_gen import generate_measurement_grid
    from .path_plan import plan_path
    from .coord_viewer_core import CoordViewerEngine
except ImportError:
    from grid_gen import generate_measurement_grid
    from path_plan import plan_path
    from coord_viewer_core import CoordViewerEngine

from nicegui import app, ui

GRID_OUTPUT_FILENAME = 'grid1.csv'
GRID_IMAGE_ROUTE = '/grid-gen-images'
GRID_IMAGE_DIR = Path(__file__).resolve().parent / 'images_grid_gen'
_grid_image_route_registered = False


def register_grid_image_files() -> None:
    """Expose bundled grid-generator diagrams through stable browser URLs."""
    global _grid_image_route_registered
    if _grid_image_route_registered:
        return
    if GRID_IMAGE_DIR.exists():
        app.add_static_files(GRID_IMAGE_ROUTE, GRID_IMAGE_DIR)
        _grid_image_route_registered = True


def grid_image_url(filename: str) -> str:
    register_grid_image_files()
    return f'{GRID_IMAGE_ROUTE}/{filename}'


def build_grid_gen_ui(get_current_pos_callback=None, on_grid_saved_callback=None):
    """
    Builds the Grid Generator UI.
    :param get_current_pos_callback: A function that returns a CylindricalPosition
                                     representing the current machine location.
    :param on_grid_saved_callback: Optional callback receiving the generated grid
                                   filename after a successful save.
    """
    register_grid_image_files()
    
    def set_position_from_callback(r_input, phi_input, z_input, btn, default_r, default_phi, default_z):
        if get_current_pos_callback:
            pos = get_current_pos_callback()
            if pos is not None:
                r_input.set_value(pos.r())
                phi_input.set_value(pos.t())
                z_input.set_value(pos.z())
                btn.props('color=green')
                ui.notify("Position captured successfully.", type='positive')
            else:
                ui.notify("Scanner position is currently unavailable.", type='warning')
        else:
            # Fallback for standalone/mock mode
            ui.notify("Mock position used (Standalone Mode).", type='info')
            r_input.set_value(default_r)
            phi_input.set_value(default_phi)
            z_input.set_value(default_z)
            btn.props('color=green')

    extra_position_fields = []

    def add_extra_position_field():
        with extra_positions_container:
            with ui.row().classes('items-center w-fit gap-2 border border-gray-200 rounded p-1 bg-gray-50') as row_container:
                with ui.column().classes('w-32 gap-0 leading-tight'):
                    name_input = ui.input('Name').classes('w-full').props('dense')
                r_input = ui.number('Radius', format='%.1f').classes('w-20').props('dense')
                phi_input = ui.number('Phi', format='%.1f').classes('w-20').props('dense')
                z_input = ui.number('Height', format='%.1f').classes('w-20').props('dense')
                set_btn = ui.button(
                    'Set',
                    on_click=lambda e: set_position_from_callback(
                        r_input, phi_input, z_input, e.sender,
                        150.0, 0.0, 200.0
                    )
                ).props('size=sm')
        extra_position_fields.append({
            'name': name_input,
            'r': r_input,
            'phi': phi_input,
            'z': z_input,
            'row': row_container,
            'set_button': set_btn,
        })

    def remove_latest_extra_position_field():
        if not extra_position_fields:
            return
        field = extra_position_fields.pop()
        field['row'].delete()

    # Main container (vertical stacking)
    with ui.column().classes('w-full max-w-4xl mx-auto p-4 items-center gap-4'):
        
        # --- Top Content: Grid Generation & Planning Settings ---
        with ui.column().classes('w-full bg-white p-4 gap-3 border border-gray-200 rounded-lg shadow-md'):
            ui.label('Grid Generation & Planning').classes('text-xl font-bold mb-2')
            
            ui.label('Cylinder Physical Waypoints - Jog and Set Position').classes('text-base font-bold')

            def toggle_adv():
                vis = not adv_container.visible
                if vis:
                    adv_container.set_visibility(True)
                    adv_icon.set_name('expand_less')
                else:
                    adv_container.set_visibility(False)
                    adv_icon.set_name('expand_more')

            with ui.column().classes('w-full gap-1'):
                with ui.row().classes('w-full items-center gap-4'):
                    with ui.row().classes('items-center w-fit gap-2 border border-gray-200 rounded p-1 bg-gray-50'):
                        with ui.column().classes('w-32 gap-0 leading-tight'):
                            ui.label('Top Waypoint:').classes('font-semibold text-sm')
                            with ui.row().classes('items-center gap-1 text-blue-500 hover:text-blue-700 cursor-help transition-colors'):
                                ui.icon('help_outline', size='14px')
                                ui.label('diagram').classes('text-[10px] font-bold uppercase tracking-wider')
                                with ui.tooltip().props('content-class="bg-white p-1 border border-gray-300 shadow-xl"'):
                                    ui.image(grid_image_url('waypoint_top.png')).classes('w-64 rounded')
                        wp_top_r = ui.number('Radius', format='%.1f').classes('w-20').props('dense')
                        wp_top_phi = ui.number('Phi', format='%.1f').classes('w-20').props('dense')
                        wp_top_z = ui.number('Height', format='%.1f').classes('w-20').props('dense')
                        ui.button('Set', on_click=lambda e: set_position_from_callback(wp_top_r, wp_top_phi, wp_top_z, e.sender, 200.0, 45.0, 350.0)).props('size=sm')
                    g_pts = ui.number('Total Points', value=1000, format='%d').classes('flex-1').props('dense outlined bg-color=white')

                with ui.row().classes('w-full items-center gap-4'):
                    with ui.row().classes('items-center w-fit gap-2 border border-gray-200 rounded p-1 bg-gray-50'):
                        with ui.column().classes('w-32 gap-0 leading-tight'):
                            ui.label('Bottom Waypoint:').classes('font-semibold text-sm')
                            with ui.row().classes('items-center gap-1 text-blue-500 hover:text-blue-700 cursor-help transition-colors'):
                                ui.icon('help_outline', size='14px')
                                ui.label('diagram').classes('text-[10px] font-bold uppercase tracking-wider')
                                with ui.tooltip().props('content-class="bg-white p-1 border border-gray-300 shadow-xl"'):
                                    ui.image(grid_image_url('waypoint_bottom.png')).classes('w-64 rounded')
                        wp_bot_r = ui.number('Radius', format='%.1f').classes('w-20').props('dense')
                        wp_bot_phi = ui.number('Phi', format='%.1f').classes('w-20').props('dense')
                        wp_bot_z = ui.number('Height', format='%.1f').classes('w-20').props('dense')
                        ui.button('Set', on_click=lambda e: set_position_from_callback(wp_bot_r, wp_bot_phi, wp_bot_z, e.sender, 40.0, 0.0, -50.0)).props('size=sm')
                    g_az_dens = ui.number('Azimuth Density Ratio', value=1.0, format='%.2f').classes('flex-1').props('dense outlined bg-color=white')

                with ui.row().classes('w-full items-end gap-4'):
                    with ui.column().classes('w-fit gap-1'):
                        with ui.row().classes('items-center w-fit gap-2 border border-gray-200 rounded p-1 bg-gray-50'):
                            with ui.column().classes('w-32 gap-0 leading-tight'):
                                ui.label('Tweeter Point:').classes('font-semibold text-sm')
                                with ui.row().classes('items-center gap-1 text-blue-500 hover:text-blue-700 cursor-help transition-colors'):
                                    ui.icon('help_outline', size='14px')
                                    ui.label('diagram').classes('text-[10px] font-bold uppercase tracking-wider')
                                    with ui.tooltip().props('content-class="bg-white p-1 border border-gray-300 shadow-xl"'):
                                        ui.image(grid_image_url('tweeter_point.png')).classes('w-64 rounded')
                            wp_twt_r = ui.number('Radius', format='%.1f').classes('w-20').props('dense')
                            wp_twt_phi = ui.number('Phi', format='%.1f').classes('w-20').props('dense')
                            wp_twt_z = ui.number('Height', format='%.1f').classes('w-20').props('dense')
                            ui.button('Set', on_click=lambda e: set_position_from_callback(wp_twt_r, wp_twt_phi, wp_twt_z, e.sender, 150.0, 0.0, 250.0)).props('size=sm')
                        extra_positions_container = ui.column().classes('w-fit gap-1')
                        with ui.row().classes('items-center gap-1 self-start'):
                            ui.button(icon='add', on_click=add_extra_position_field).props('flat round dense color=primary')
                            ui.button(icon='remove', on_click=remove_latest_extra_position_field).props('flat round dense color=negative')
                    with ui.row().classes('flex-1 h-fit items-center justify-between bg-gray-100 hover:bg-gray-200 cursor-pointer rounded border border-gray-200 p-1 px-2 transition-colors').on('click', toggle_adv):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('settings', size='sm').classes('text-gray-700')
                            ui.label('Advanced Settings').classes('text-gray-800 text-sm font-medium')
                        adv_icon = ui.icon('expand_more', size='sm').classes('text-gray-700')

            with ui.column().classes('w-full bg-gray-50 rounded border border-gray-200 p-4 gap-2 mt-2') as adv_container:
                adv_container.set_visibility(False)
                with ui.row().classes('w-full gap-4'):
                    g_rad = ui.number('Cyl Radius (mm)', value=200.0, format='%.1f').classes('flex-1')
                    g_ht = ui.number('Cyl Height (mm)', value=500.0, format='%.1f').classes('flex-1')
                    g_phi_min = ui.number('Phi Min (deg)', value=-170.0, format='%.1f').classes('flex-1')
                    g_phi_max = ui.number('Phi Max (deg)', value=170.0, format='%.1f').classes('flex-1')
                with ui.row().classes('w-full gap-4'):
                    g_bot_cut = ui.number('Bottom Cutoff (mm)', value=30.0, format='%.1f').classes('flex-1')
                    g_d_theta = ui.number('Delta Theta (deg)', value=7.5, format='%.1f').classes('flex-1')
                    g_wall_th = ui.number('Wall Thickness (mm)', value=50.0).classes('flex-1')
                    g_cap_frac = ui.input('Cap Fraction', value='Auto').classes('flex-1')
                with ui.row().classes('w-full gap-4'):
                    g_p_side = ui.number('P_side', value=0.5).classes('flex-1')
                    g_p_caps = ui.number('P_caps', value=0.8).classes('flex-1')
                    g_cap_tol = ui.input('Cap Tol (mm)', value='Auto').classes('flex-1')
                    g_az_wc = ui.number('Az Weight Center', value=0.0).classes('flex-1')
                with ui.row().classes('w-full gap-4 items-center'):
                    g_z_rot = ui.number('Z Rot 2nd Spiral', value=90.0).classes('flex-1')
                    g_snake = ui.select(['up', 'down'], value='up', label='Side Snake Start').classes('flex-1')
                    g_rev_sp = ui.checkbox('Generate Reverse Spiral', value=True)
                    g_flip_p = ui.checkbox('Flip Poles', value=False)
                    g_z_mid = ui.checkbox('Z Midpoint = 0', value=True)

            def do_generate_and_plan():
                try:
                    def get_wp(r, phi, z):
                        if r.value is None or phi.value is None or z.value is None:
                            return None
                        return (float(r.value), float(phi.value), float(z.value))

                    def get_additional_positions():
                        positions = []
                        for index, field in enumerate(extra_position_fields, start=1):
                            pos = get_wp(field['r'], field['phi'], field['z'])
                            if pos is None:
                                continue
                            name = str(field['name'].value or '').strip() or f"point_{index}"
                            positions.append((name, pos))
                        return positions
                        
                    cf_val = None if str(g_cap_frac.value).strip().lower() in ('auto', 'none', '') else float(g_cap_frac.value)
                    
                    grid_data = generate_measurement_grid(
                        cyl_radius_mm=g_rad.value,
                        cyl_height_mm=g_ht.value,
                        num_points=int(g_pts.value),
                        wall_thickness_mm=g_wall_th.value,
                        bottom_cutoff_mm=g_bot_cut.value,
                        cap_fraction=cf_val,
                        P_side=g_p_side.value,
                        P_caps=g_p_caps.value,
                        generate_reverse_spiral=g_rev_sp.value,
                        z_rotation_deg=g_z_rot.value,
                        flip_poles=g_flip_p.value,
                        z_midpoint_zero=g_z_mid.value,
                        phi_min_deg=g_phi_min.value,
                        phi_max_deg=g_phi_max.value,
                        azimuth_density_ratio=g_az_dens.value,
                        azimuth_weight_center_deg=g_az_wc.value,
                        tweeter_pos=get_wp(wp_twt_r, wp_twt_phi, wp_twt_z),
                        additional_positions=get_additional_positions(),
                        top_crit_pos=get_wp(wp_top_r, wp_top_phi, wp_top_z),
                        bot_crit_pos=get_wp(wp_bot_r, wp_bot_phi, wp_bot_z)
                    )
                    
                    ct_val_str = str(g_cap_tol.value).strip().lower()
                    cap_tol = g_wall_th.value + 1.0 if ct_val_str in ('auto', '') else float(g_cap_tol.value)
                    
                    output_csv = os.path.abspath(os.path.join(os.getcwd(), GRID_OUTPUT_FILENAME))
                    
                    planned_data = plan_path(
                        input_data=grid_data,
                        cap_tol_mm=cap_tol,
                        delta_theta_deg=g_d_theta.value,
                        side_snake_start=g_snake.value,
                        output_path=output_csv,
                        show_replay=False
                    )
                    
                    engine.load_data(planned_data)
                    scrub_slider.props(f'max={max(0, engine.N - 1)}')
                    scrub_slider.set_value(0)
                    if on_grid_saved_callback:
                        on_grid_saved_callback(GRID_OUTPUT_FILENAME)
                    ui.notify(f"Grid successfully generated and saved.", type='positive')
                    
                except Exception as e:
                    print(f"Failed to generate grid: {e}")
                    ui.notify(f"Error generating grid: {e}", type='negative')

            ui.button('Generate & Plan Path', on_click=do_generate_and_plan, icon='check').classes('mt-4 w-full bg-green-700 text-white')

        # Container for controls (visually above the viewer)
        controls_container = ui.column().classes('w-full bg-white p-4 gap-3 border border-gray-200 rounded-lg shadow-md')

        # Container for the Viewer Engine (visually below the controls)
        viewer_container = ui.row().classes('w-full justify-start')

        # Initialize the Viewer Engine
        with viewer_container:
            # Initialize empty or load existing file if available
            initial_data = GRID_OUTPUT_FILENAME if os.path.exists(GRID_OUTPUT_FILENAME) else ('MySpeaker_scan_path.csv' if os.path.exists('MySpeaker_scan_path.csv') else None)
            engine = CoordViewerEngine(input_data=initial_data)

        # Populate the playback controls
        with controls_container:
            # Row 1: Scrub
            with ui.row().classes('w-full items-center gap-3 flex-nowrap'):
                ui.label("Scrub:").classes('text-sm font-semibold text-gray-700 w-12')
                scrub_slider = ui.slider(min=0, max=max(0, engine.N-1), value=0, on_change=lambda e: engine.set_current_index(e.value)).classes('flex-grow').props('dense')

            # Row 2: Combined Controls
            with ui.row().classes('w-full items-center justify-between flex-wrap gap-2'):
                # Left side: Playback controls
                with ui.row().classes('items-center gap-1'):
                    ui.button('|<', on_click=lambda: engine.rewind()).props('outline size=sm padding="xs sm"')
                    ui.button('<', on_click=lambda: engine.step_back()).props('outline size=sm padding="xs sm"')
                    
                    def toggle_play():
                        if engine.is_playing:
                            engine.pause()
                        else:
                            engine.play()
                            
                    play_btn = ui.button('Play', on_click=toggle_play).props('color=primary size=sm padding="xs md"').classes('w-16')
                    ui.button('>', on_click=lambda: engine.step_fwd()).props('outline size=sm padding="xs sm"')
                    
                    ui.label('Rate:').classes('text-sm font-semibold text-gray-700 ml-1')
                    ui.number(value=600, on_change=lambda e: engine.set_speed(e.value)).props('dense outlined bg-color=white size=sm').classes('w-16')

                # Middle: View Controls
                with ui.row().classes('items-center gap-1'):
                    ui.button('Top', on_click=lambda: engine.set_view(90, 0)).props('outline size=sm padding="xs sm"')
                    ui.button('Front', on_click=lambda: engine.set_view(0, -90)).props('outline size=sm padding="xs sm"')
                    ui.button('Side', on_click=lambda: engine.set_view(0, 0)).props('outline size=sm padding="xs sm"')
                    
                    ui.checkbox('Ortho', value=False, on_change=lambda e: engine.set_ortho(e.value)).props('dense size=sm').classes('text-sm text-gray-700 ml-1')
                    ui.checkbox('Bounds', value=True, on_change=lambda e: engine.set_bounds_visibility(e.value)).props('dense size=sm').classes('text-sm text-gray-700 ml-1')

                # Right side: Rotate and More
                with ui.row().classes('items-center gap-1'):
                    ui.label('Rot:').classes('text-sm font-semibold text-gray-700')
                    rot_ang_input = ui.number(value=45, step=5).props('dense outlined bg-color=white size=sm').classes('w-14')
                    
                    def toggle_rotate():
                        if engine.is_rotating:
                            engine.stop_rotation()
                            rot_btn.props('outline')
                        else:
                            engine.start_rotation(rot_ang_input.value)
                            rot_btn.props(remove='outline')

                    rot_btn = ui.button('Rotate', on_click=toggle_rotate).props('outline color=primary size=sm padding="xs sm"')
                    
                    with ui.button('More', icon='tune').props('outline color=primary size=sm padding="xs sm"').classes('ml-2'):
                        with ui.menu().classes('p-4 flex flex-col gap-3'):
                            ui.label('Advanced Display Settings').classes('text-sm font-bold text-gray-800 border-b pb-1 mb-1')
                            
                            with ui.row().classes('items-center gap-2'):
                                ui.label('Tail Len:').classes('text-sm font-semibold text-gray-700 w-16')
                                ui.slider(min=10, max=200, value=50, on_change=lambda e: engine.set_tail_length(e.value)).props('dense').classes('w-24')
                                
                            ui.checkbox('Fade History', value=False, on_change=lambda e: engine.set_history_mode(e.value)).props('dense size=sm').classes('text-sm text-gray-700')

                            with ui.row().classes('items-center gap-2'):
                                ui.label('Rot Speed:').classes('text-sm font-semibold text-gray-700 w-16')
                                ui.number(value=5, min=1, max=180, step=1, on_change=lambda e: engine.set_rotation_speed(e.value)).props('dense outlined bg-color=white size=sm suffix="deg/s"').classes('w-28')
                            
                            with ui.row().classes('items-center gap-2'):
                                ui.label('Color:').classes('text-sm font-semibold text-gray-700 w-16')
                                ui.slider(min=0.0, max=1.0, step=0.01, value=0.5, on_change=lambda e: engine.set_color(e.value)).props('dense').classes('w-24')
                                
                            with ui.row().classes('items-center gap-2'):
                                ui.label('Opacity:').classes('text-sm font-semibold text-gray-700 w-16')
                                ui.slider(min=0.1, max=1.0, step=0.05, value=1.0, on_change=lambda e: engine.set_alpha(e.value)).props('dense').classes('w-24')

        # Background timer to keep the UI in sync with the core engine's playback state
        def sync_ui():
            # Update scrub slider position during playback
            if engine.is_playing:
                scrub_slider.value = engine.curr_idx
                
            # Make sure play/pause button matches engine state 
            # (handles cases where engine automatically stops at end of file)
            expected_play_text = 'Pause' if engine.is_playing else 'Play'
            if play_btn.text != expected_play_text:
                play_btn.text = expected_play_text
                play_btn.props(f'color={"negative" if engine.is_playing else "primary"}')

        ui.timer(0.1, sync_ui)


if __name__ in {"__main__", "__mp_main__"}:
    register_grid_image_files()

    @ui.page('/')
    def main_page():
        ui.page_title("3D Replay Viewer (Plotly)")

        with ui.header().classes('bg-indigo-800 p-4'):
            ui.label("Interactive 3D Coordinate Replay (Plotly Edition)").classes('text-xl font-bold text-white')

        build_grid_gen_ui()
        
    # Run the app
    ui.run(title="Coord Viewer Plotly Demo", port=8080, dark=False)
