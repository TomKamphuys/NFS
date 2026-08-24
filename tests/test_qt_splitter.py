import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtTest import QTest

from harmonic_drive_qt.qt_compat import QApplication, Qt, QWidget
from harmonic_drive_qt.styles import app_stylesheet
from harmonic_drive_qt.widgets import LocallyPaintedSplitter


def _app():
    return QApplication.instance() or QApplication([])


def test_splitter_hover_does_not_repaint_adjacent_content():
    app = _app()

    class PaintCounter(QWidget):
        def __init__(self):
            super().__init__()
            self.paint_count = 0

        def paintEvent(self, event):  # noqa: N802 - Qt override
            self.paint_count += 1
            super().paintEvent(event)

    old_stylesheet = app.styleSheet()
    app.setStyleSheet(app_stylesheet())
    splitter = LocallyPaintedSplitter(Qt.Orientation.Horizontal)
    content = PaintCounter()
    splitter.addWidget(content)
    splitter.addWidget(QWidget())
    splitter.setHandleWidth(8)
    splitter.resize(1200, 400)
    splitter.show()
    try:
        for _ in range(3):
            app.processEvents()
        content.paint_count = 0
        handle = splitter.handle(1)
        for _ in range(5):
            QTest.mouseMove(handle, handle.rect().center(), delay=0)
            app.processEvents()
            QTest.mouseMove(content, content.rect().center(), delay=0)
            app.processEvents()

        assert content.paint_count == 0
        assert "QSplitter::handle:horizontal:hover" not in app.styleSheet()
        assert "QSplitter::handle:vertical:hover" not in app.styleSheet()
    finally:
        splitter.close()
        app.setStyleSheet(old_stylesheet)
