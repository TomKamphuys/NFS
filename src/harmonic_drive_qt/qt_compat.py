"""Small PySide6 import shim with a friendly error."""

try:
    from PySide6.QtCore import Qt, QTimer, QObject, Signal, Slot, QRunnable, QThreadPool, QSize
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF
    from PySide6.QtWidgets import (
        QAbstractSpinBox,
        QApplication,
        QButtonGroup,
        QComboBox,
        QCheckBox,
        QDoubleSpinBox,
        QDialog,
        QFileDialog,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListView,
        QMainWindow,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QSlider,
        QScrollArea,
        QSizePolicy,
        QSplitter,
        QStackedWidget,
        QTabWidget,
        QTextEdit,
        QStyle,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - depends on local env
    if exc.name == "PySide6":
        raise SystemExit(
            "PySide6 is not installed. Add it with:\n\n"
            "  uv add PySide6\n\n"
            "Then run:\n\n"
            "  uv run python -m harmonic_drive_qt.main\n"
        ) from exc
    raise
