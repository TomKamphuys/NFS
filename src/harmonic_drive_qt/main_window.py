"""Main native Qt window."""

from __future__ import annotations

from pathlib import Path
import configparser

from harmonic_drive import project

from .audio_setup_pane import AudioSetupPane
from .backend import BackendManager
from .control_pane import ControlPane
from .grid_pane import GridGeneratorPane
from .live_capture import LiveCapturePane
from .settings_dialog import SettingsDialog
from .styles import danger_button, primary_button
from .qt_compat import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
)


NO_SESSION_FOLDER_TEXT = "No session folder selected"


def _config_bool(config_file: str, section: str, key: str, fallback: bool) -> bool:
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.read(config_file)
    try:
        return parser.getboolean(section, key, fallback=fallback)
    except ValueError:
        return fallback


class PlaceholderPane(QWidget):
    def __init__(self, title: str, detail: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        label = QLabel(title)
        label.setStyleSheet("font-size: 18px; font-weight: 700;")
        body = QLabel(detail)
        body.setWordWrap(True)
        body.setStyleSheet("color: #4b5563;")
        layout.addWidget(label)
        layout.addWidget(body)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self, backend: BackendManager, config_file: str, parent=None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.config_file = config_file
        self.menu_auto_hide = _config_bool(config_file, "app", "auto_hide_left_menu", False)
        self.setWindowTitle("HALS Control")
        self.resize(1800, 1040)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 18, 18, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        header = QFrame()
        header.setObjectName("SessionHeader")
        header.setMinimumHeight(50)
        header.setMaximumHeight(54)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 6, 14, 6)
        
        # Hamburger menu button (mocked as simple button for now)
        self.menu_toggle = QPushButton("☰")
        self.menu_toggle.setFixedSize(44, 32)
        self.menu_toggle.setStyleSheet("background: transparent; color: #60a5fa; font-size: 24px; border: none; font-weight: bold; padding: 0;")
        self.menu_toggle.clicked.connect(self.toggle_menu)

        title = QLabel("HALS Control")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #ffffff; border: none;")

        self.project_path = QLineEdit(self._project_path_display())
        self.project_path.setReadOnly(True)
        self.project_path.setFixedWidth(440)
        self.project_path.setFixedHeight(34)
        
        browse = QPushButton("NEW/LOAD SESSION")
        browse.setFixedHeight(34)
        primary_button(browse)
        browse.clicked.connect(self.browse_project)

        self.project_name = QLineEdit(project.get_project_name())
        self.project_name.setFixedWidth(320)
        self.project_name.setFixedHeight(34)
        self.project_name.textChanged.connect(self._sync_project_name_from_field)

        save = QPushButton("SAVE")
        save.setFixedHeight(34)
        primary_button(save)
        save.clicked.connect(self.save_project_context)

        header_layout.addWidget(self.menu_toggle)
        header_layout.addSpacing(10)
        header_layout.addWidget(title)
        header_layout.addSpacing(52)
        
        lbl_folder = QLabel("Session Folder")
        lbl_folder.setStyleSheet("font-weight: bold; color: #ffffff; border: none;")
        header_layout.addWidget(lbl_folder)
        header_layout.addWidget(self.project_path)
        header_layout.addWidget(browse)
        
        header_layout.addSpacing(10)
        
        lbl_save = QLabel("Save Name")
        lbl_save.setStyleSheet("font-weight: bold; color: #ffffff; border: none;")
        header_layout.addWidget(lbl_save)
        header_layout.addWidget(self.project_name)
        header_layout.addWidget(save)
        header_layout.addStretch(1)
        
        root_layout.addWidget(header)

        # Main content area below header
        content_area = QWidget()
        content_layout = QVBoxLayout(content_area)
        content_layout.setContentsMargins(0, 12, 0, 0)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        self.splitter = splitter
        content_layout.addWidget(splitter, 1)
        root_layout.addWidget(content_area, 1)

        left_region = QWidget()
        left_region.setMinimumWidth(420)
        left_region.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_layout = QHBoxLayout(left_region)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.menu = self._build_menu()
        self.left_stack = QStackedWidget()
        self.left_stack.setMinimumWidth(220)
        self.control_pane = ControlPane(self.backend, require_session_folder=self.require_session_folder)
        self.audio_pane = AudioSetupPane(self.backend, self.config_file, show_live_capture=self.show_live_capture)
        self.audio_pane.saved.connect(lambda: self.live_capture.refresh_all())
        self.left_stack.addWidget(self.control_pane)
        self.left_stack.addWidget(self.audio_pane)
        left_layout.addWidget(self.menu)
        left_layout.addWidget(self.left_stack, 1)

        self.right_stack = QStackedWidget()
        self.right_stack.setMinimumWidth(420)
        self.right_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.live_capture = LiveCapturePane(self.backend, self.config_file)
        self.control_pane.measurement_saved.connect(self.live_capture.refresh_all)
        self.grid_pane = GridGeneratorPane(
            self.backend,
            self.config_file,
            require_session_folder=self.require_session_folder,
        )
        self.grid_pane.grid_saved.connect(self.on_grid_saved)
        self.right_stack.addWidget(self.live_capture)
        self.right_stack.addWidget(self.grid_pane)

        splitter.addWidget(left_region)
        splitter.addWidget(self.right_stack)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        QTimer.singleShot(0, self.set_default_splitter_sizes)

        self.show_machine()
        self.show_live_capture()
        self._apply_menu_auto_hide(initial=True)

    def set_default_splitter_sizes(self) -> None:
        available = max(1, self.splitter.width())
        left_width = min(820, max(1, available - 1))
        self.splitter.setSizes([left_width, available - left_width])

    def toggle_menu(self) -> None:
        self.menu.setVisible(not self.menu.isVisible())

    def _build_menu(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("SideMenu")
        frame.setMinimumWidth(190)
        frame.setMaximumWidth(230)
        layout = QVBoxLayout(frame)
        label = QLabel("Views")
        label.setStyleSheet("font-weight: 700; color: #4b5563;")
        layout.addWidget(label)

        self.audio_button = self._menu_button("Audio Setup", self.show_audio)
        self.grid_button = self._menu_button("Grid Generator", self.show_grid)
        self.machine_button = self._menu_button("Machine Control", self.show_machine)
        self.live_button = self._menu_button("Live Capture", self.show_live_capture)
        self.settings_button = self._menu_button("Settings", self.show_settings)
        self.shutdown_button = self._menu_button("Shutdown Program", self.close, danger=True)
        for button in (
            self.audio_button,
            self.grid_button,
            self.machine_button,
            self.live_button,
            self.settings_button,
            self.shutdown_button,
        ):
            layout.addWidget(button)
        layout.addStretch(1)
        return frame

    def _project_path_display(self) -> str:
        return NO_SESSION_FOLDER_TEXT if project.is_temporary_project_dir() else str(project.get_project_dir())

    def _menu_button(self, text: str, callback, danger: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setMinimumHeight(38)
        button.setStyleSheet(
            "QPushButton { text-align: left; padding-left: 10px; border: 0; }"
            "QPushButton:hover { background: #e5e7eb; }"
            + ("QPushButton { color: #b91c1c; }" if danger else "")
        )
        if danger:
            danger_button(button)
        button.clicked.connect(lambda: self._run_menu_action(callback))
        return button

    def _run_menu_action(self, callback) -> None:
        callback()
        self._apply_menu_auto_hide()

    def _apply_menu_auto_hide(self, initial: bool = False) -> None:
        self.menu_auto_hide = _config_bool(self.config_file, "app", "auto_hide_left_menu", False)
        if self.menu_auto_hide:
            self.menu.setVisible(False)
        elif not initial:
            self.menu.setVisible(True)

    def _mark_active(self, *active_buttons: QPushButton) -> None:
        for button in (
            self.audio_button,
            self.grid_button,
            self.machine_button,
            self.live_button,
            self.settings_button,
        ):
            if button in active_buttons:
                button.setStyleSheet(
                    "QPushButton { text-align: left; padding-left: 10px; border: 0; "
                    "background: #dbeafe; color: #1d4ed8; font-weight: 700; }"
                )
            else:
                button.setStyleSheet(
                    "QPushButton { text-align: left; padding-left: 10px; border: 0; }"
                    "QPushButton:hover { background: #e5e7eb; }"
                )

    def show_machine(self) -> None:
        self.left_stack.setCurrentWidget(self.control_pane)
        self._mark_active(self.machine_button, self.live_button if self.right_stack.currentWidget() is self.live_capture else self.grid_button)

    def show_audio(self) -> None:
        self.left_stack.setCurrentWidget(self.audio_pane)
        self.show_live_capture()
        self._mark_active(self.audio_button, self.live_button)

    def show_live_capture(self) -> None:
        self.right_stack.setCurrentWidget(self.live_capture)
        self.live_capture.set_active(True)
        self._mark_active(
            self.audio_button if self.left_stack.currentWidget() is self.audio_pane else self.machine_button,
            self.live_button,
        )

    def show_grid(self) -> None:
        self.right_stack.setCurrentWidget(self.grid_pane)
        self.live_capture.set_active(False)
        self.left_stack.setCurrentWidget(self.control_pane)
        self._mark_active(self.machine_button, self.grid_button)

    def show_settings(self, initial_section: str | None = None) -> None:
        self._mark_active(self.settings_button)
        dialog = SettingsDialog(self.config_file, self.on_settings_applied, self)
        if initial_section is not None:
            dialog.select_section(initial_section)
        dialog.exec()
        # Restore active mark based on current view
        if self.left_stack.currentWidget() is self.control_pane:
            if self.right_stack.currentWidget() is self.live_capture:
                self._mark_active(self.machine_button, self.live_button)
            else:
                self._mark_active(self.machine_button, self.grid_button)
        else:
            self._mark_active(self.audio_button, self.live_button)

    def on_settings_applied(self) -> None:
        self.backend.load()
        self._rebuild_control_pane()
        self._apply_menu_auto_hide()
        self.grid_pane.refresh_from_config()
        self.live_capture.refresh_all()

    def _rebuild_control_pane(self) -> None:
        was_current = self.left_stack.currentWidget() is self.control_pane
        old_pane = self.control_pane
        self.left_stack.removeWidget(old_pane)
        old_pane.deleteLater()
        self.control_pane = ControlPane(self.backend, require_session_folder=self.require_session_folder)
        self.control_pane.measurement_saved.connect(self.live_capture.refresh_all)
        self.left_stack.insertWidget(0, self.control_pane)
        if was_current:
            self.left_stack.setCurrentWidget(self.control_pane)

    def _rebuild_audio_pane(self) -> None:
        if not hasattr(self, "left_stack") or not hasattr(self, "audio_pane"):
            return
        was_current = self.left_stack.currentWidget() is self.audio_pane
        old_pane = self.audio_pane
        self.left_stack.removeWidget(old_pane)
        try:
            old_pane.auto_apply_timer.stop()
            old_pane.cal_timer.stop()
        except Exception:
            pass
        old_pane.deleteLater()
        self.audio_pane = AudioSetupPane(self.backend, self.config_file, show_live_capture=self.show_live_capture)
        self.audio_pane.saved.connect(lambda: self.live_capture.refresh_all())
        self.left_stack.insertWidget(1, self.audio_pane)
        if was_current:
            self.left_stack.setCurrentWidget(self.audio_pane)

    def _rebuild_grid_pane(self) -> None:
        if not hasattr(self, "right_stack") or not hasattr(self, "grid_pane"):
            return
        was_current = self.right_stack.currentWidget() is self.grid_pane
        old_pane = self.grid_pane
        self.right_stack.removeWidget(old_pane)
        try:
            old_pane.shutdown()
            old_pane.sync_timer.stop()
        except Exception:
            pass
        old_pane.deleteLater()
        self.grid_pane = GridGeneratorPane(
            self.backend,
            self.config_file,
            require_session_folder=self.require_session_folder,
        )
        self.grid_pane.grid_saved.connect(self.on_grid_saved)
        self.right_stack.addWidget(self.grid_pane)
        if was_current:
            self.right_stack.setCurrentWidget(self.grid_pane)

    def browse_project(self) -> None:
        initial = "" if self.project_path.text() == NO_SESSION_FOLDER_TEXT else self.project_path.text()
        directory = QFileDialog.getExistingDirectory(self, "Measurement Folder", initial)
        if directory:
            self.activate_project_context(directory, preserve_entered_name=False)

    def require_session_folder(self) -> bool:
        self._flush_audio_pane_changes()
        if not project.is_temporary_project_dir():
            self._sync_project_name_from_field()
            return True

        result = QMessageBox.question(
            self,
            "No Session Folder Selected",
            "Choose or create a Session Folder before saving grids or measurements.\n\n"
            "Sine Tone and Test Sweep can still run without a folder.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Open,
            QMessageBox.StandardButton.Open,
        )
        if result != QMessageBox.StandardButton.Open:
            return False

        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Session Folder",
            str(project.get_default_project_root(self.config_file)),
        )
        if not directory:
            return False

        self.activate_project_context(directory, preserve_entered_name=True)
        return not project.is_temporary_project_dir()

    def _sync_project_name_from_field(self) -> None:
        if not hasattr(self, "project_name"):
            return
        project.set_project_name(self.project_name.text())

    def _flush_audio_pane_changes(self) -> None:
        audio_pane = getattr(self, "audio_pane", None)
        flush = getattr(audio_pane, "flush_pending_changes", None)
        if callable(flush):
            flush()

    def activate_project_context(self, path: str | Path, preserve_entered_name: bool) -> None:
        """Load/use a selected session folder without writing a project JSON."""
        path = Path(path).expanduser()
        entered_name = self.project_name.text()
        try:
            project.set_project_dir(path, self.config_file)
            if preserve_entered_name:
                project.set_project_name(entered_name)
            project.apply_to_config(self.config_file)
            project.ensure_output_dirs()
        except Exception:
            pass
        self.backend.set_project_dir(path)
        self.project_path.setText(str(project.get_project_dir()))
        self.project_name.blockSignals(True)
        self.project_name.setText(project.get_project_name())
        self.project_name.blockSignals(False)
        self.control_pane.refresh_grid_readout()
        self._rebuild_audio_pane()
        self._rebuild_grid_pane()
        self.live_capture.refresh_all()

    def save_project_context(self) -> None:
        self._flush_audio_pane_changes()
        path_text = self.project_path.text().strip()
        if not path_text or path_text == NO_SESSION_FOLDER_TEXT:
            return
        path = Path(path_text).expanduser()
        try:
            resolved_path = path.resolve()
            if resolved_path != project.get_project_dir().resolve():
                project.set_project_dir(resolved_path, self.config_file)
            project.set_project_name(self.project_name.text())
            project.apply_to_config(self.config_file)
            project.ensure_output_dirs()
            project.save_project()
        except Exception:
            pass
        self.backend.set_project_dir(path)
        self.project_path.setText(str(project.get_project_dir()))
        self.project_name.setText(project.get_project_name())
        self.control_pane.refresh_grid_readout()
        self._rebuild_audio_pane()
        self.live_capture.refresh_all()

    def on_grid_saved(self, _filename: str, _grid_vars: dict) -> None:
        self._sync_project_name_from_field()
        self.control_pane.refresh_grid_readout()
        self.live_capture.refresh_all()
        try:
            self.backend.load()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Reload Config",
                f"Grid saved, but backend reload failed:\n\n{exc}",
            )

    def closeEvent(self, event) -> None:  # noqa: N802
        for pane in (getattr(self, "live_capture", None), getattr(self, "grid_pane", None)):
            if pane is not None and hasattr(pane, "shutdown"):
                try:
                    pane.shutdown()
                except Exception:
                    pass
        self.backend.shutdown()
        super().closeEvent(event)
