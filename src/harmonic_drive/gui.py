import argparse
import os

from nicegui import app, ui

from grid_generator.grid_gen_gui import build_grid_gen_ui
from harmonic_drive import control, live_capture


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


@ui.page('/')
def main_page():
    status_label, finish_splash = _build_splash()

    async def start_load():
        await control.load_app(status_label, finish_splash)

    ui.timer(0, start_load, once=True)

    log_dialog = control.build_log_dialog()

    with ui.splitter(value=50).classes('w-full h-screen items-stretch') as splitter:
        with splitter.before:
            with ui.row().classes('w-full h-full min-w-0 flex-nowrap'):
                with ui.column().classes(
                    'w-56 h-full bg-gray-100 border-r border-gray-300 p-2 gap-2'
                ) as menu:
                    ui.label('Views').classes('text-sm font-bold text-gray-600')
                    live_capture_button = ui.button(
                        'Live Capture',
                        icon='graphic_eq',
                    ).props('flat align=left').classes('w-full justify-start')
                    grid_button = ui.button(
                        'Grid Generator',
                        icon='grid_on',
                    ).props('flat align=left').classes('w-full justify-start')
                menu_visible = {'value': True}

                def toggle_menu():
                    menu_visible['value'] = not menu_visible['value']
                    menu.set_visibility(menu_visible['value'])

                with ui.column().classes('w-full h-full min-w-0'):
                    with ui.row().classes('w-full items-center'):
                        ui.button(
                            icon='menu',
                            on_click=toggle_menu,
                        ).props('flat round dense').classes('m-1')
                        ui.label('Control').classes('text-sm font-bold text-gray-600')
                    control.build_control_pane(log_dialog)

        with splitter.after:
            with ui.element('div').classes('w-full h-full min-w-0'):
                live_capture_panel = ui.column().classes('w-full h-full')
                grid_panel = ui.column().classes('w-full h-full overflow-auto')

                with live_capture_panel:
                    live_capture.build_live_capture()

                with grid_panel:
                    build_grid_gen_ui(
                        get_current_pos_callback=_get_current_position,
                        on_grid_saved_callback=control.use_generated_grid_file,
                    )
                    grid_panel.set_visibility(False)

                def show_live_capture():
                    live_capture_panel.set_visibility(True)
                    grid_panel.set_visibility(False)
                    menu_visible['value'] = False
                    menu.set_visibility(False)
                    live_capture_button.props('color=primary')
                    grid_button.props(remove='color')
                    live_capture.update_live_capture_plots()

                def show_grid():
                    live_capture_panel.set_visibility(False)
                    grid_panel.set_visibility(True)
                    menu_visible['value'] = False
                    menu.set_visibility(False)
                    grid_button.props('color=primary')
                    live_capture_button.props(remove='color')

                live_capture_button.on('click', show_live_capture)
                grid_button.on('click', show_grid)
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
    control.set_on_config_loaded(live_capture.update_live_capture_plots)

    static_images_path = os.path.join(os.getcwd(), 'images')
    if os.path.exists(static_images_path):
        app.add_static_files('/images', static_images_path)

    favicon = os.path.join(static_images_path, 'icon.png')
    ui.run(
        reload=False,
        title='HALS',
        favicon=favicon if os.path.exists(favicon) else None,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
