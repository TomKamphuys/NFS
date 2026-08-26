"""Custom lightweight Qt widgets for live monitoring."""

from __future__ import annotations

from .qt_compat import (
    QColor,
    QFont,
    QFrame,
    QPainter,
    QPen,
    QSplitter,
    QSplitterHandle,
    Qt,
    QWidget,
)


class LocallyPaintedSplitterHandle(QSplitterHandle):
    """Splitter handle whose hover repaint is confined to the handle itself."""

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        hovered = self.underMouse()
        painter.fillRect(self.rect(), QColor("#dbeafe" if hovered else "#eef6ff"))
        painter.setPen(QPen(QColor("#5b9bd8" if hovered else "#cfe4fa")))
        rect = self.rect()
        if self.orientation() == Qt.Orientation.Horizontal:
            painter.drawLine(rect.left(), rect.top(), rect.left(), rect.bottom())
            painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())
        else:
            painter.drawLine(rect.left(), rect.top(), rect.right(), rect.top())
            painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())


class LocallyPaintedSplitter(QSplitter):
    """QSplitter that avoids stylesheet hover invalidation of child widgets."""

    def createHandle(self) -> QSplitterHandle:  # noqa: N802 - Qt override
        return LocallyPaintedSplitterHandle(self.orientation(), self)


class LevelMeter(QFrame):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self.label = label
        self.fill_color = "#ffffff"
        self.border_color = "#d1d5db"
        self.rms_db = -120.0
        self.peak_db = -120.0
        self.clipped = False
        self.setMinimumHeight(58)
        self.setFrameShape(QFrame.Shape.StyledPanel)

    def set_tone(self, fill: str, border: str) -> None:
        self.fill_color = fill
        self.border_color = border
        self.update()

    def set_values(self, label: str, rms_db: float, peak_db: float, clipped: bool) -> None:
        self.label = label
        self.rms_db = float(rms_db)
        self.peak_db = float(peak_db)
        self.clipped = bool(clipped)
        self.update()

    @staticmethod
    def _pct(value: float) -> float:
        return max(0.0, min(1.0, (float(value) + 60.0) / 60.0))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = self.rect().adjusted(1, 1, -2, -2)
        painter.setPen(QPen(QColor(self.border_color)))
        painter.setBrush(QColor(self.fill_color))
        painter.drawRoundedRect(frame, 4, 4)
        rect = self.rect().adjusted(8, 6, -8, -6)
        painter.setPen(QPen(QColor("#374151")))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.drawText(rect.left(), rect.top() + 11, self.label)

        bar = rect.adjusted(0, 20, -92, -10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e5e7eb"))
        painter.drawRoundedRect(bar, 4, 4)
        fill = bar.adjusted(0, 0, -int(bar.width() * (1.0 - self._pct(self.rms_db))), 0)
        painter.setBrush(QColor("#ef4444") if self.clipped else QColor("#3b82f6"))
        painter.drawRoundedRect(fill, 4, 4)

        peak_x = bar.left() + int(bar.width() * self._pct(self.peak_db))
        painter.setPen(QPen(QColor("#dc2626"), 2))
        painter.drawLine(peak_x, bar.top(), peak_x, bar.bottom())

        painter.setPen(QPen(QColor("#6b7280")))
        painter.setFont(QFont("Segoe UI", 7))
        painter.drawText(bar.left(), rect.bottom(), "-60 dB")
        painter.drawText(bar.right() - 24, rect.bottom(), "0 dB")

        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QPen(QColor("#dc2626") if self.clipped else QColor("#374151")))
        painter.drawText(rect.right() - 84, rect.top() + 28, f"RMS {self._fmt(self.rms_db)}")
        painter.drawText(rect.right() - 84, rect.top() + 44, f"Peak {self._fmt(self.peak_db)}")

    @staticmethod
    def _fmt(value: float) -> str:
        return "-inf" if value <= -119 else f"{value:.1f}"
