"""Shared Qt styling for the native HALS UI."""

from __future__ import annotations

from pathlib import Path


BLUE = "#5b9bd8"
BLUE_DARK = "#3978bd"
RED = "#c90024"
ORANGE = "#f59e0b"
PANEL_BORDER = "#d6dde8"
TEXT = "#0f172a"
MUTED = "#64748b"


def app_stylesheet() -> str:
    image_path = (Path.cwd() / "images" / "bar_bg2_qt_dulled.png").as_posix()
    toggle_off = (Path(__file__).resolve().parent / "icons" / "toggle-off.svg").as_posix()
    toggle_on = (Path(__file__).resolve().parent / "icons" / "toggle-on.svg").as_posix()
    disclosure_closed = (Path(__file__).resolve().parent / "icons" / "disclosure-closed.svg").as_posix()
    disclosure_open = (Path(__file__).resolve().parent / "icons" / "disclosure-open.svg").as_posix()
    return f"""
    QWidget {{
        background: #ffffff;
        color: {TEXT};
        font-family: "Segoe UI";
        font-size: 10pt;
    }}
    QMainWindow, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{
        background: #ffffff;
    }}
    QLabel {{
        background: transparent;
        border: none;
    }}
    QFrame#SessionHeader {{
        background-color: #06111e;
        background-image: url("{image_path}");
        background-position: center;
        border-radius: 6px;
        color: #ffffff;
    }}
    QFrame#SideMenu {{
        background: #f3f4f6;
        border-right: 1px solid #d1d5db;
    }}
    QPushButton {{
        background: #ffffff;
        border: 1px solid #bfd0e4;
        border-radius: 4px;
        color: #2563a7;
        font-weight: 700;
        padding: 6px 10px;
    }}
    QPushButton:hover {{
        background: #eef6ff;
        border-color: #7db2e8;
    }}
    QPushButton:pressed {{
        background: #dbeafe;
    }}
    QPushButton#PrimaryButton {{
        background: {BLUE};
        border-color: {BLUE};
        color: #ffffff;
    }}
    QPushButton#PrimaryButton:hover {{
        background: {BLUE_DARK};
        border-color: {BLUE_DARK};
    }}
    QPushButton#DangerButton {{
        background: {RED};
        border-color: {RED};
        color: #ffffff;
    }}
    QPushButton#WarningButton {{
        background: #f4c542;
        border-color: #f4c542;
        color: #ffffff;
    }}
    QLineEdit, QComboBox, QDoubleSpinBox {{
        background: #ffffff;
        border: 1px solid #bfc8d4;
        border-radius: 4px;
        color: #111827;
        min-height: 24px;
        padding: 4px 8px;
    }}
    QLineEdit:read-only {{
        background: rgba(248, 250, 252, 0.94);
    }}
    QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {{
        background: #f1f5f9;
        border-color: #d8dee8;
        color: #94a3b8;
    }}
    QComboBox:disabled::drop-down {{
        border: 0;
    }}
    QComboBox::drop-down {{
        border: 0;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background: #ffffff;
        color: #111827;
        selection-background-color: #dbeafe;
        selection-color: #111827;
        border: 1px solid #bfc8d4;
    }}
    QGroupBox {{
        background: #ffffff;
        border: 0;
        border-top: 1px solid #e2e8f0;
        margin-top: 12px;
        padding-top: 14px;
        font-size: 15px;
        font-weight: 800;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 0;
        top: 0;
        padding-right: 8px;
        color: #000000;
    }}
    QFrame#Card {{
        background: #ffffff;
        border: 1px solid {PANEL_BORDER};
        border-radius: 6px;
    }}
    QFrame#PlotCard {{
        background: #ffffff;
        border: 1px solid {PANEL_BORDER};
        border-radius: 4px;
    }}
    QProgressBar {{
        background: #e5e7eb;
        border: 1px solid #d1d5db;
        border-radius: 4px;
        color: #ffffff;
        font-size: 11px;
        font-weight: 700;
        height: 20px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: #4b7dff;
        border-radius: 3px;
    }}
    QSlider::groove:horizontal {{
        background: #c7c7c7;
        height: 4px;
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {BLUE};
        width: 14px;
        height: 14px;
        margin: -5px 0;
        border-radius: 7px;
    }}
    QScrollBar:vertical {{
        background: #eef6ff;
        border: 1px solid #d6e8fb;
        border-radius: 5px;
        margin: 0;
        width: 12px;
    }}
    QScrollBar::handle:vertical {{
        background: {BLUE};
        border-radius: 5px;
        min-height: 34px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {BLUE_DARK};
    }}
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0;
        border: 0;
        background: transparent;
    }}
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    QScrollBar:horizontal {{
        background: #eef6ff;
        border: 1px solid #d6e8fb;
        border-radius: 5px;
        height: 12px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {BLUE};
        border-radius: 5px;
        min-width: 34px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {BLUE_DARK};
    }}
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {{
        width: 0;
        border: 0;
        background: transparent;
    }}
    QScrollBar::add-page:horizontal,
    QScrollBar::sub-page:horizontal {{
        background: transparent;
    }}
    QCheckBox {{
        spacing: 8px;
        background: transparent;
    }}
    QCheckBox:disabled {{
        color: #94a3b8;
    }}
    QCheckBox::indicator {{
        width: 44px;
        height: 24px;
        image: url("{toggle_off}");
        border: none;
    }}
    QCheckBox::indicator:checked {{
        image: url("{toggle_on}");
    }}
    QGroupBox::indicator {{
        width: 12px;
        height: 12px;
        image: url("{disclosure_closed}");
        border: none;
    }}
    QGroupBox::indicator:checked {{
        image: url("{disclosure_open}");
    }}
    QSplitter::handle:horizontal {{
        background: #eef6ff;
        border-left: 1px solid #cfe4fa;
        border-right: 1px solid #cfe4fa;
    }}
    QSplitter::handle:horizontal:hover {{
        background: #dbeafe;
        border-left-color: {BLUE};
        border-right-color: {BLUE};
    }}
    QSplitter::handle:vertical {{
        background: #eef6ff;
        border-top: 1px solid #cfe4fa;
        border-bottom: 1px solid #cfe4fa;
    }}
    QSplitter::handle:vertical:hover {{
        background: #dbeafe;
        border-top-color: {BLUE};
        border-bottom-color: {BLUE};
    }}
    """


def toggle_style() -> str:
    toggle_off = (Path(__file__).resolve().parent / "icons" / "toggle-off.svg").as_posix()
    toggle_on = (Path(__file__).resolve().parent / "icons" / "toggle-on.svg").as_posix()
    toggle_disabled_on = (Path(__file__).resolve().parent / "icons" / "toggle-disabled-on.svg").as_posix()
    return (
        "QCheckBox { border: none; background: transparent; spacing: 8px; }"
        "QCheckBox:disabled { color: #94a3b8; }"
        f"QCheckBox::indicator {{ width: 44px; height: 24px; image: url(\"{toggle_off}\"); border: none; }}"
        f"QCheckBox::indicator:checked {{ image: url(\"{toggle_on}\"); }}"
        f"QCheckBox::indicator:checked:disabled {{ image: url(\"{toggle_disabled_on}\"); }}"
    )


COMBO_POPUP_STYLE = (
    "QListView { background: #ffffff; color: #111827; "
    "border: 1px solid #bfc8d4; outline: 0; }"
    "QListView::item { min-height: 24px; padding: 3px 8px; }"
    "QListView::item:selected { background: #dbeafe; color: #111827; }"
)


def light_combo(combo) -> None:
    from .qt_compat import QListView

    view = QListView(combo)
    view.setStyleSheet(COMBO_POPUP_STYLE)
    combo.setView(view)


def primary_button(button) -> None:
    button.setObjectName("PrimaryButton")
    button.setStyleSheet(
        f"QPushButton {{ background: {BLUE}; border: 1px solid {BLUE}; "
        "border-radius: 4px; color: #ffffff; font-weight: 800; padding: 7px 11px; }"
        f"QPushButton:hover {{ background: {BLUE_DARK}; border-color: {BLUE_DARK}; }}"
    )


def danger_button(button) -> None:
    button.setObjectName("DangerButton")
    button.setStyleSheet(
        f"QPushButton {{ background: {RED}; border: 1px solid {RED}; "
        "border-radius: 4px; color: #ffffff; font-weight: 800; padding: 7px 11px; }"
        "QPushButton:hover { background: #a8001e; border-color: #a8001e; }"
    )


def warning_button(button) -> None:
    button.setObjectName("WarningButton")
    button.setStyleSheet(
        "QPushButton { background: #f4c542; border: 1px solid #f4c542; "
        "border-radius: 4px; color: #ffffff; font-weight: 800; padding: 7px 11px; }"
        "QPushButton:hover { background: #e5b834; border-color: #e5b834; }"
    )
