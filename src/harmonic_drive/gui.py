import argparse
import asyncio
import os
import tempfile
from pathlib import Path

from nicegui import app, run, ui

from grid_generator.grid_gen_gui import build_grid_gen_ui, register_grid_image_files
from harmonic_drive import audio_setup, control, live_capture, project
from harmonic_drive.config_editor import open_config_editor


NO_SESSION_FOLDER_TEXT = "No session folder selected"


def resolve_session_folder_value(
    displayed_value: str | None,
    active_project_dir: Path,
    is_temporary_project_dir: bool,
) -> str | None:
    value = str(displayed_value or "").strip()
    if value and value != NO_SESSION_FOLDER_TEXT:
        return value
    if not is_temporary_project_dir:
        return str(active_project_dir.resolve())
    return None


ui.add_head_html(
    '<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" rel="stylesheet">',
    shared=True,
)
ui.add_head_html(
    '<link rel="icon" type="image/png" href="/images/icon.png">',
    shared=True,
)

ui.add_css("""
@keyframes alarm_blink {
  0%   { opacity: 1; }
  50%  { opacity: 0.15; }
  100% { opacity: 1; }
}
.alarm_blink {
  animation: alarm_blink 0.6s linear infinite;
}
.jog-grid {
  display: grid;
  grid-template-columns: 64px repeat(4, 72px) 72px repeat(4, 72px);
  gap: 6px;
  align-items: center;
}
.jog-hdr {
  font-size: 0.85rem;
  font-weight: 600;
  color: #374151;
}
.jog-hdr-left  { grid-column: 2 / span 4; text-align: left; }
.jog-hdr-stop  { grid-column: 6; text-align: center; }
.jog-hdr-right { grid-column: 7 / span 4; text-align: right; }
.jog-axis {
  font-weight: 800;
  color: #111827;
  line-height: 1.05;
}
.jog-unit {
  font-size: 0.75rem;
  font-weight: 700;
  color: #374151;
  margin-top: 2px;
}
.jog-btn {
  width: 72px;
  min-height: 38px;
  font-weight: 800;
}
.jog-stop {
  width: 72px;
  min-height: 38px;
  font-weight: 900;
}
.cmd-row {
  display: grid;
  grid-template-columns: repeat(5, 120px);
  gap: 18px;
  align-items: stretch;
}
.cmd-btn {
  min-height: 56px;
  font-weight: 800;
  letter-spacing: 0.5px;
}
.cmd-btn-blue {
  background: #8fa9db !important;
  color: #0b1220 !important;
  border: 1px solid #5d6b86 !important;
}
.side-menu-button .q-btn__content {
  display: flex;
  flex-wrap: nowrap;
  justify-content: flex-start;
  gap: 10px;
  font-size: 0.875rem;
  line-height: 1.25rem;
  white-space: nowrap;
  width: 100%;
  min-width: 0;
}
.side-menu-button .q-icon {
  width: 24px;
  min-width: 24px;
  font-size: 20px;
}
.side-menu-button .block {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
""", shared=True)


def _build_splash():
    with ui.element('div').style(
        'position: fixed; top: 25%; left: 25%; width: 50%; height: 50%; '
        'z-index: 9999; background: transparent; opacity: 1;'
    ) as splash:
        ui.image('/images/splash.png').style(
            'width: 100%; height: 100%; object-fit: cover; '
            'position: absolute; top: 0; left: 0;'
        )
        with ui.column().classes('w-full items-start justify-end h-full p-8 relative'):
            with ui.row().classes('items-center'):
                status_label = ui.label('Initializing').classes(
                    'text-xl font-bold text-white shadow-sm'
                )
                dots_label = ui.label('.').classes(
                    'text-xl font-bold text-white'
                )

            def update_dots():
                dots_label.set_text('.' * ((len(dots_label.text) % 3) + 1))

            ui.timer(0.5, update_dots)

    async def finish_splash():
        ui.timer(2.0, lambda: splash.style('transition: opacity 1s; opacity: 0;'))

        def safe_delete():
            try:
                splash.delete()
            except Exception:
                pass

        ui.timer(3.0, safe_delete)

    return status_label, finish_splash


def _get_current_position():
    try:
        scanner = control.get_scanner()
        return scanner.get_position() if scanner else None
    except Exception:
        return None


def _default_measurement_folder(config_file: str, save_name: str | None = None) -> Path:
    name = project.sanitize_project_name(save_name or project.get_project_name())
    return project.get_default_project_root(config_file) / name


def _native_folder_picker(initial_dir: str) -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        return filedialog.askdirectory(
            title='Select Session Folder',
            initialdir=initial_dir,
            mustexist=False,
        )
    finally:
        root.destroy()


async def _browse_measurement_folder(path_input, title_input, on_project_loaded=None):
    current_value = str(path_input.value or "").strip()
    if current_value and current_value != NO_SESSION_FOLDER_TEXT:
        initial_dir = str(Path(current_value).expanduser().resolve())
    else:
        initial_dir = str(project.get_default_project_root(control.scanner_app.config_file))
    try:
        selected = await run.io_bound(_native_folder_picker, initial_dir)
    except Exception as exc:
        ui.notify(f'Could not open folder browser: {exc}', type='negative')
        return False
    if selected:
        path_input.set_value(selected)
        project.set_project_dir(selected, control.scanner_app.config_file)
        live_capture.reset_live_capture_session()
        project.apply_to_config(control.scanner_app.config_file)
        control.scanner_app.reload_config_ui()
        title_input.set_value(project.get_project_name())
        if on_project_loaded is not None:
            on_project_loaded()
        ui.notify('Measurement folder loaded', type='positive')
        return True
    return False


async def _browse_project_folder(path_input, title_input, on_project_loaded=None):
    initial_dir = str(Path(path_input.value or os.getcwd()).expanduser().resolve())
    try:
        selected = await run.io_bound(_native_folder_picker, initial_dir)
    except Exception as exc:
        ui.notify(f'Could not open folder browser: {exc}', type='negative')
        return

    if not selected:
        return

    path_input.set_value(selected)
    project.set_project_dir(selected, control.scanner_app.config_file)
    live_capture.reset_live_capture_session()
    project.apply_to_config(control.scanner_app.config_file)
    control.scanner_app.reload_config_ui()
    title_input.set_value(project.get_project_name())
    if on_project_loaded is not None:
        on_project_loaded()
    ui.notify('Project folder loaded', type='positive')


@ui.page('/')
def main_page():
    status_label, finish_splash = _build_splash()

    async def start_load():
        await control.load_app(status_label, finish_splash)

    ui.timer(0, start_load, once=True)

    log_dialog = control.build_log_dialog()

    with ui.column().classes('w-full h-screen min-h-0 gap-0'):
        with ui.row().classes('w-full h-14 shrink-0 items-center gap-3 px-3 bg-gray-100 border-b border-gray-300'):
            menu_button = ui.button(icon='menu').props('flat round dense')
            ui.label('HALS Control').classes('text-lg font-bold text-gray-800')
            ui.element('div').classes('w-6')
            ui.label('Session Folder').classes('text-sm font-semibold text-gray-600')
            project_path_input = ui.input(
                value=NO_SESSION_FOLDER_TEXT,
            ).props('dense outlined readonly').classes('w-[360px] max-w-[34vw]')
            ui.button(
                'New/Load Session',
                icon='folder_open',
                on_click=lambda: select_session_folder(),
            ).props('color=primary dense')
            ui.label('Save Name').classes('text-sm font-semibold text-gray-600')
            project_title_input = ui.input(value=project.get_project_name()).props('dense outlined').classes('w-48')

            def session_folder_value() -> str | None:
                return resolve_session_folder_value(
                    project_path_input.value,
                    project.get_project_dir(),
                    project.is_temporary_project_dir(),
                )

            def has_session_folder() -> bool:
                return session_folder_value() is not None

            async def select_session_folder(rebuild=True):
                selected = await _browse_measurement_folder(
                    project_path_input,
                    project_title_input,
                    on_project_loaded=(lambda: rebuild_grid_panel()) if rebuild else None,
                )
                return selected

            async def require_session_folder() -> bool:
                if has_session_folder():
                    return True

                decision = asyncio.Future()
                with ui.dialog() as dialog, ui.card().classes("w-[420px] max-w-full"):
                    ui.label("No session folder selected").classes("text-lg font-bold")
                    ui.label(
                        "Choose or create a Session Folder before saving grids or "
                        "measurements. Test Sweep can still run without a folder."
                    ).classes("text-sm text-gray-700")

                    async def browse_and_close():
                        selected = await select_session_folder(rebuild=False)
                        if selected and not decision.done():
                            decision.set_result(True)
                            dialog.close()

                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button(
                            "Cancel",
                            on_click=lambda: (
                                decision.set_result(False),
                                dialog.close(),
                            ),
                        ).props("flat")
                        ui.button(
                            "Browse",
                            icon="folder_open",
                            on_click=browse_and_close,
                        ).props("color=primary")
                dialog.open()
                return await decision

            async def save_project_snapshot():
                if not await require_session_folder():
                    return
                project.set_project_name(project_title_input.value)
                saved_dir = project.save_project_to(
                    project_path_input.value,
                    project_title_input.value,
                    control.scanner_app.config_file,
                )
                project_path_input.set_value(str(saved_dir))
                rebuild_grid_panel()
                ui.notify(f'Measurement set saved to {saved_dir}', type='positive')

            control.set_measurement_set_context(
                lambda: project_title_input.value,
                session_folder_value,
                require_session_folder,
            )

            with ui.button(
                'Save',
                icon='save',
                on_click=save_project_snapshot,
            ).props('color=primary dense'):
                ui.tooltip('Saves: current project settings, measurement grid.')
            ui.element('div').classes('flex-grow')

        with ui.splitter(value=50).classes('w-full flex-1 min-h-0 items-stretch') as splitter:
            with splitter.before:
                with ui.row().classes('w-full h-full min-w-0 flex-nowrap'):
                    with ui.column().classes(
                        'w-56 h-full bg-gray-100 border-r border-gray-300 p-2 gap-2'
                    ) as menu:
                        ui.label('Views').classes('text-sm font-bold text-gray-600')
                        audio_button = ui.button(
                            'Audio Setup',
                            icon='settings_voice',
                        ).props('flat align=left no-caps').classes('side-menu-button w-full h-10 justify-start text-sm')
                        grid_button = ui.button(
                            'Grid Generator',
                            icon='grid_on',
                        ).props('flat align=left no-caps').classes('side-menu-button w-full h-10 justify-start text-sm')
                        machine_button = ui.button(
                            'Machine Control',
                            icon='precision_manufacturing',
                        ).props('flat align=left no-caps').classes('side-menu-button w-full h-10 justify-start text-sm')
                        live_capture_button = ui.button(
                            'Live Capture',
                            icon='graphic_eq',
                        ).props('flat align=left no-caps').classes('side-menu-button w-full h-10 justify-start text-sm')
                        ui.element('div').classes('h-4 shrink-0')
                        settings_button = ui.button(
                            'Settings',
                            icon='settings',
                        ).props('flat align=left no-caps').classes('side-menu-button w-full h-10 justify-start text-sm')
                    menu_visible = {'value': True}

                    def toggle_menu():
                        menu_visible['value'] = not menu_visible['value']
                        menu.set_visibility(menu_visible['value'])

                    def hide_menu():
                        menu_visible['value'] = False
                        menu.set_visibility(False)

                    menu_button.on('click', toggle_menu)

                    with ui.column().classes('w-full h-full min-w-0'):
                        machine_panel = ui.column().classes('w-full h-full min-w-0 overflow-auto')
                        audio_panel = ui.column().classes('w-full h-full min-w-0 overflow-auto')

                        with machine_panel:
                            control.build_control_pane(log_dialog)
                        with audio_panel:
                            audio_setup.build_audio_setup_pane(
                                control.scanner_app.config_file,
                                show_live_capture=lambda: show_live_capture(),
                            )
                            audio_panel.set_visibility(False)

            with splitter.after:
                with ui.element('div').classes('w-full h-full min-h-0 min-w-0 overflow-hidden'):
                    live_capture_panel = ui.column().classes(
                        'w-full h-full min-h-0 min-w-0 overflow-hidden'
                    )
                    grid_panel = ui.column().classes('w-full h-full min-h-0 overflow-auto')

                    with live_capture_panel:
                        live_capture.build_live_capture(control.scanner_app.config_file)

                    def current_grid_filename():
                        grid_vars = project.get_project_data().get('grid_vars', {})
                        if isinstance(grid_vars, dict) and grid_vars.get('output_filename'):
                            return grid_vars['output_filename']
                        return project.get_grid_filename()

                    async def activate_measurement_folder(create=True):
                        if not has_session_folder():
                            if not create:
                                return None
                            if not await require_session_folder():
                                return None
                        session_value = session_folder_value()
                        if session_value is None:
                            return None
                        path = Path(session_value).expanduser().resolve()
                        project_path_input.set_value(str(path))
                        if create:
                            project.set_project_dir(path, control.scanner_app.config_file)
                            project.set_project_name(project_title_input.value)
                        return path

                    def rebuild_grid_panel():
                        was_visible = grid_panel.visible
                        grid_panel.clear()
                        with grid_panel:
                            build_grid_gen_ui(
                                get_current_pos_callback=_get_current_position,
                                on_grid_saved_callback=control.use_generated_grid_file,
                                initial_grid_vars=project.get_project_data().get('grid_vars', {}),
                                output_directory=activate_measurement_folder,
                                output_filename=current_grid_filename,
                            )
                        grid_panel.set_visibility(False)
                        grid_panel.set_visibility(was_visible)

                    with grid_panel:
                        rebuild_grid_panel()

                    def clear_button_colors():
                        for button in (machine_button, audio_button, grid_button, live_capture_button, settings_button):
                            button.props(remove='color')

                    def show_machine():
                        machine_panel.set_visibility(True)
                        audio_panel.set_visibility(False)
                        clear_button_colors()
                        machine_button.props('color=primary')
                        hide_menu()

                    def show_audio_setup():
                        machine_panel.set_visibility(False)
                        audio_panel.set_visibility(True)
                        show_live_capture()
                        clear_button_colors()
                        audio_button.props('color=primary')
                        hide_menu()

                    def show_live_capture():
                        live_capture_panel.set_visibility(True)
                        grid_panel.set_visibility(False)
                        live_capture_button.props('color=primary')
                        grid_button.props(remove='color')
                        live_capture.update_live_capture_plots()
                        hide_menu()

                    def show_grid():
                        machine_panel.set_visibility(True)
                        audio_panel.set_visibility(False)
                        live_capture_panel.set_visibility(False)
                        grid_panel.set_visibility(True)
                        machine_button.props('color=primary')
                        grid_button.props('color=primary')
                        audio_button.props(remove='color')
                        live_capture_button.props(remove='color')
                        hide_menu()

                    def show_settings():
                        clear_button_colors()
                        settings_button.props('color=primary')
                        hide_menu()
                        open_config_editor(
                            control.scanner_app.config_file,
                            control.scanner_app.reload_config_ui,
                        )

                    live_capture_button.on('click', show_live_capture)
                    grid_button.on('click', show_grid)
                    machine_button.on('click', show_machine)
                    audio_button.on('click', show_audio_setup)
                    settings_button.on('click', show_settings)
                    show_machine()
                    show_live_capture()


def main():
    parser = argparse.ArgumentParser(description='Near-field scanner UI')
    parser.add_argument(
        '--config',
        default='config.ini',
        help='Path to the configuration file',
    )
    args, _ = parser.parse_known_args()

    control.initialize_app(args.config)
    project.set_project_dir(Path(tempfile.gettempdir()) / "HALS_working_project", args.config)
    control.set_on_config_loaded(live_capture.update_live_capture_plots)

    static_images_path = os.path.join(os.getcwd(), 'images')
    if os.path.exists(static_images_path):
        app.add_static_files('/images', static_images_path)
    register_grid_image_files()

    favicon = os.path.join(static_images_path, 'icon.png')
    ui.run(
        reload=False,
        title='HALS',
        favicon=favicon if os.path.exists(favicon) else None,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
