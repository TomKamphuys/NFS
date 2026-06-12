import argparse
import asyncio
import os
import tempfile
from pathlib import Path

from nicegui import app, run, ui

from grid_generator.grid_gen_gui import build_grid_gen_ui, register_grid_image_files
from harmonic_drive import audio_setup, control, live_capture, project, reconnect_debug
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
.alt-motion-panel {
  width: min(100%, 780px);
  box-sizing: border-box;
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0;
  padding: 0;
  background: #000000;
  border: 2px solid #374151;
  border-radius: 8px;
  box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.22), 0 4px 6px -4px rgba(0, 0, 0, 0.22);
  overflow: hidden;
}
.alt-top-grid {
  display: grid;
  grid-template-columns: 36px repeat(4, minmax(0, 1fr));
  gap: 0;
  align-items: stretch;
  width: 100%;
}
.alt-jog-grid {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 0;
  align-items: stretch;
  width: 100%;
}
.alt-move-title,
.alt-status-controls,
.alt-axis-group {
  border-right: 1px solid #1f2937;
}
.alt-move-title {
  min-width: 0;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(96, 165, 250, 0.55);
  font-size: 0.74rem;
  font-weight: 800;
  line-height: 1.15;
  text-transform: uppercase;
  font-family: 'Share Tech Mono', monospace;
  letter-spacing: 0;
}
.alt-axis-group {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 7px 8px 8px 8px;
}
.alt-status-controls {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: center;
  gap: 7px;
  padding: 7px 0 8px 0;
  border-right: 0;
}
.alt-status-spacer {
  height: 0.83rem;
}
.alt-axis-label {
  color: #d1d5db;
  font-size: 0.72rem;
  font-weight: 700;
  line-height: 1.15;
  text-transform: uppercase;
  text-align: center;
  letter-spacing: 0;
}
.alt-axis-header {
  position: relative;
  width: 100%;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 9px;
  align-items: center;
  color: #d1d5db;
  font-size: 0.72rem;
  font-weight: 700;
  line-height: 1.15;
  text-transform: uppercase;
  text-align: center;
  letter-spacing: 0;
}
.alt-axis-title {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  white-space: nowrap;
}
.alt-axis-sign {
  color: #f9fafb;
  font-size: 1.05rem;
  font-weight: 900;
  line-height: 1;
  justify-self: center;
}
.alt-axis-buttons {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 9px;
}
.alt-motion-btn {
  width: 100%;
  min-width: 0;
  height: 44px;
  min-height: 44px;
  font-weight: 800;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.12);
}
.alt-motion-btn .q-btn__content {
  gap: 6px;
}
.alt-motion-btn .q-icon {
  font-size: 30px;
}
.alt-jog-rows {
  min-width: 0;
  display: grid;
  grid-template-rows: repeat(3, auto);
  width: 100%;
}
.alt-jog-row {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 58px minmax(0, 1fr);
  column-gap: 8px;
  align-items: center;
  padding: 8px 8px;
  border-bottom: 1px solid #1f2937;
}
.alt-jog-row:last-child {
  border-bottom: 0;
}
.alt-jog-axis {
  min-width: 0;
  height: 35px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #d1d5db;
  font-size: 0.68rem;
  font-weight: 800;
  line-height: 1.08;
  text-align: center;
  text-transform: uppercase;
}
.alt-jog-unit {
  color: rgba(209, 213, 219, 0.82);
  font-size: 0.6rem;
  font-weight: 700;
  margin-top: 3px;
}
.alt-jog-side {
  min-width: 0;
  display: grid;
  grid-template-columns: 20px repeat(6, minmax(34px, 1fr));
  gap: 7px;
  align-items: center;
}
.alt-jog-side-right {
  grid-template-columns: repeat(6, minmax(34px, 1fr)) 20px;
}
.alt-jog-direction {
  color: #d1d5db;
  font-size: 0.74rem;
  font-weight: 800;
  line-height: 1;
  text-align: center;
  text-transform: uppercase;
  white-space: nowrap;
}
.alt-jog-steps {
  min-width: 0;
  display: contents;
}
.alt-jog-step-btn {
  width: 100%;
  min-width: 0;
  color: #ffffff !important;
  border: 1px solid rgba(255, 255, 255, 0.16) !important;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.08);
}
.alt-step-row,
.alt-command-row {
  width: 100%;
  box-sizing: border-box;
  display: grid;
  align-items: center;
  border-top: 1px solid #1f2937;
  padding: 7px 18px 0 54px;
}
.alt-step-row {
  position: relative;
  grid-template-columns: repeat(7, 48px);
  gap: 8px;
  min-height: 42px;
  padding-top: 7px;
  padding-bottom: 7px;
  justify-content: center;
  padding-left: 18px;
  padding-right: 18px;
}
.alt-step-label {
  position: absolute;
  right: calc(50% + 200px);
  top: 50%;
  transform: translateY(-50%);
  width: 78px;
  color: #d1d5db;
  font-size: 0.72rem;
  font-weight: 700;
  line-height: 1.15;
  text-transform: uppercase;
  text-align: center;
  letter-spacing: 0;
}
.alt-step-btn {
  width: 48px;
  height: 35px;
  min-height: 35px;
  padding: 0;
  font-size: 0.86rem;
  font-weight: 800;
}
.alt-command-row {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  padding: 7px 24px 10px 24px;
}
.alt-command-row-5 {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
.alt-command-row-6 {
  grid-template-columns: repeat(6, minmax(0, 1fr));
}
.alt-command-btn {
  width: 100%;
  justify-self: center;
  height: 45px;
  min-height: 45px;
  padding: 0 10px;
  font-size: 0.86rem;
  font-weight: 800;
}
.alt-command-btn .q-btn__content {
  gap: 8px;
  flex-wrap: nowrap;
}
.alt-command-btn .block {
  line-height: 1.05;
}
.alt-command-btn .q-icon {
  font-size: 21px;
}
.alt-status-controls .alt-command-btn {
  width: calc(100% - 24px);
  align-self: center;
  height: 44px;
  min-height: 44px;
  font-size: 0.86rem;
}
.alt-status-controls .alt-command-btn .q-icon {
  font-size: 21px;
}
.session-header {
  position: relative;
  overflow: hidden;
  background-image:
    linear-gradient(90deg, rgba(4, 10, 18, 0.88), rgba(8, 18, 30, 0.68) 42%, rgba(4, 10, 18, 0.9)),
    url('/images/bar_bg2.png');
  background-size: cover;
  background-position: 50% 50%;
  border-radius: 8px;
  color: #f8fafc;
}
.session-header::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  border-bottom: 1px solid rgba(125, 211, 252, 0.35);
  box-shadow: inset 0 -12px 24px rgba(14, 165, 233, 0.16);
}
.session-header > * {
  position: relative;
  z-index: 1;
}
.session-header .q-field--outlined .q-field__control {
  background: rgba(248, 250, 252, 0.9);
}
.session-header .q-field--readonly .q-field__control::before {
  border-style: solid;
}
.session-header .q-field__native,
.session-header .q-field__input {
  color: #111827;
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
        ui.image('/images/splash.png').props('tag=img').style(
            'width: 100%; height: 100%; object-fit: contain; '
            'position: absolute; top: 0; left: 0;'
        )

    async def finish_splash():
        splash.style('transition: opacity 0.6s; opacity: 0;')

        def safe_delete():
            try:
                splash.delete()
            except Exception:
                pass

        ui.timer(0.7, safe_delete, once=True)

    return finish_splash


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
    reconnect_debug.log_page_created()
    reconnect_debug.install_browser_probe()
    finish_splash = _build_splash()

    async def start_load():
        await control.load_app(finish_splash)

    ui.timer(0, start_load, once=True)

    log_dialog = control.build_log_dialog()

    with ui.column().classes('w-full h-screen min-h-0 gap-0'):
        with ui.row().classes(
            'session-header h-14 shrink-0 items-center gap-3 px-3 mx-2 mt-2'
        ).style('width: calc(100% - 1rem);'):
            menu_button = ui.button(icon='menu').props('flat round dense')
            ui.label('HALS Control').classes('text-lg font-bold text-white')
            ui.element('div').classes('w-6')
            ui.label('Session Folder').classes('text-sm font-semibold text-slate-100')
            project_path_input = ui.input(
                value=NO_SESSION_FOLDER_TEXT,
            ).props('dense outlined readonly').classes('w-[360px] max-w-[34vw]')
            ui.button(
                'New/Load Session',
                icon='folder_open',
                on_click=lambda: select_session_folder(),
            ).props('color=primary dense')
            ui.label('Save Name').classes('text-sm font-semibold text-slate-100')
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
                    on_project_loaded=(
                        lambda: rebuild_grid_panel()
                    ) if rebuild else None,
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
                        shutdown_button = ui.button(
                            'Shutdown Program',
                            icon='power_settings_new',
                            on_click=control.shutdown_from_ui,
                        ).props('flat align=left no-caps color=negative').classes(
                            'side-menu-button w-full h-10 justify-start text-sm'
                        )
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

                        def cancel_panel_timers(panel):
                            descendants = getattr(panel, "descendants", None)
                            elements = descendants() if callable(descendants) else []
                            for element in list(elements):
                                cancel = getattr(element, "cancel", None)
                                if callable(cancel):
                                    try:
                                        cancel()
                                    except Exception:
                                        pass

                        def rebuild_machine_panel():
                            was_visible = machine_panel.visible
                            cancel_panel_timers(machine_panel)
                            machine_panel.clear()
                            control.scanner_app.greyable_buttons = []
                            with machine_panel:
                                control.build_control_pane(log_dialog)
                            machine_panel.set_visibility(False)
                            machine_panel.set_visibility(was_visible)

                        rebuild_machine_panel()

                        def rebuild_audio_panel():
                            was_visible = audio_panel.visible
                            cancel_panel_timers(audio_panel)
                            audio_panel.clear()
                            with audio_panel:
                                audio_setup.build_audio_setup_pane(
                                    control.scanner_app.config_file,
                                    show_live_capture=lambda: show_live_capture(),
                                )
                            audio_panel.set_visibility(False)
                            audio_panel.set_visibility(was_visible)

                        rebuild_audio_panel()
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
                        cancel_panel_timers(grid_panel)
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

                    project.on_project_changed(rebuild_audio_panel)

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
                        rebuild_audio_panel()
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

                        def apply_settings():
                            control.scanner_app.reload_config_ui()
                            rebuild_machine_panel()
                            rebuild_audio_panel()

                        open_config_editor(
                            control.scanner_app.config_file,
                            apply_settings,
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
    reconnect_debug.install()
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
