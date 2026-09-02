"""Small PySide6 import shim with a friendly error."""

try:
    from PySide6.QtCore import Qt, QTimer, QObject, Signal, Slot, QRunnable, QThreadPool, QSize, QLocale, QEvent
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF
    from PySide6.QtWidgets import (
        QAbstractSpinBox,
        QApplication,
        QButtonGroup,
        QComboBox,
        QCheckBox,
        QDoubleSpinBox as _QDoubleSpinBox,
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
        QSplitterHandle,
        QStackedWidget,
        QTabWidget,
        QTextEdit,
        QStyle,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )

    class QDoubleSpinBox(_QDoubleSpinBox):
        """QDoubleSpinBox that accepts comma or dot decimals and displays dots."""

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.setLocale(QLocale(QLocale.Language.C))
            self.lineEdit().textEdited.connect(self._normalize_editor_text)

        def _normalized(self, text: str) -> str:
            return text.replace(",", ".")

        def _normalize_editor_text(self, text: str) -> None:
            if "," not in text:
                return
            cursor_pos = self.lineEdit().cursorPosition()
            self.lineEdit().setText(self._normalized(text))
            self.lineEdit().setCursorPosition(cursor_pos)

        def validate(self, text: str, pos: int):
            state, _text, _pos = super().validate(self._normalized(text), pos)
            return state, text, pos

        def valueFromText(self, text: str) -> float:
            return super().valueFromText(self._normalized(text))
except ModuleNotFoundError as exc:  # pragma: no cover - depends on local env
    if exc.name == "PySide6":
        raise SystemExit(
            "PySide6 is not installed. Add it with:\n\n"
            "  uv add PySide6\n\n"
            "Then run:\n\n"
            "  uv run python -m harmonic_drive_qt.main\n"
        ) from exc
    raise
