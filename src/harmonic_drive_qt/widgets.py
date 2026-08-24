"""Custom lightweight Qt widgets for live monitoring."""

from __future__ import annotations

import math
from typing import Iterable

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


class LinePlot(QWidget):
    """Small dependency-free line/scatter plot for the native prototype."""

    def __init__(self, title: str, x_label: str = "", y_label: str = "", parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.x_label = x_label
        self.y_label = y_label
        self.x_values: list[float] = []
        self.y_values: list[float] = []
        self.message = "Waiting for data..."
        self.scatter = False
        self.log_x = False
        self.y_range: tuple[float, float] | None = None
        self.x_range: tuple[float, float] | None = None
        self.default_y_range: tuple[float, float] | None = None
        self.default_x_range: tuple[float, float] | None = None
        self.color_points_by_y = False
        self.y_axis_mode = "linear"
        self._drag_origin: tuple[int, int] | None = None
        self._drag_current: tuple[int, int] | None = None
        self._plot_rect = None
        self._data_bounds: tuple[float, float, float, float] | None = None
        self.setMinimumHeight(130)

    def set_data(
        self,
        x_values: Iterable[float],
        y_values: Iterable[float],
        *,
        title: str | None = None,
        scatter: bool = False,
        log_x: bool = False,
        x_range: tuple[float, float] | None = None,
        y_range: tuple[float, float] | None = None,
        y_axis_mode: str | None = None,
    ) -> None:
        self.x_values = [float(x) for x in x_values]
        self.y_values = [float(y) for y in y_values]
        if title is not None:
            self.title = title
        self.scatter = scatter
        self.log_x = log_x
        self.x_range = x_range
        self.y_range = y_range
        self.default_x_range = x_range
        self.default_y_range = y_range
        self.y_axis_mode = y_axis_mode or "linear"
        self.message = ""
        self.update()

    def clear_data(self, message: str, title: str | None = None) -> None:
        self.x_values = []
        self.y_values = []
        self.message = message
        if title is not None:
            self.title = title
        self.update()

    def reset_zoom(self) -> None:
        self.x_range = self.default_x_range
        self.y_range = self.default_y_range
        self.update()

    def _value_to_color(self, value: float, ymin: float, ymax: float) -> QColor:
        pct = 0.5 if ymax == ymin else max(0.0, min(1.0, (value - ymin) / (ymax - ymin)))
        low = (37, 99, 235)
        mid = (20, 184, 166)
        high = (244, 114, 182)
        if pct < 0.5:
            local = pct * 2.0
            a, b = low, mid
        else:
            local = (pct - 0.5) * 2.0
            a, b = mid, high
        rgb = [round(a[i] + (b[i] - a[i]) * local) for i in range(3)]
        return QColor(*rgb)

    def _tick_label(self, value: float, is_x: bool) -> str:
        if is_x and self.log_x:
            actual = 10 ** value
            if actual >= 1000:
                return f"{actual / 1000:g}k"
            return f"{actual:g}"
        if abs(value) >= 100:
            return f"{value:.0f}"
        if abs(value) >= 10:
            return f"{value:.1f}"
        return f"{value:.2g}"

    def _symmetric_dbfs_label(self, value: float, ymax_abs: float) -> str:
        magnitude = abs(value) / max(ymax_abs, 1e-12)
        if magnitude <= 1e-6:
            return "-inf"
        db = 20.0 * math.log10(min(1.0, magnitude))
        if abs(db) < 0.05:
            return "0"
        if db <= -10:
            return f"{db:.0f}"
        return f"{db:.1f}".rstrip("0").rstrip(".")

    def _screen_to_data(self, px: int, py: int) -> tuple[float, float] | None:
        if self._plot_rect is None or self._data_bounds is None:
            return None
        plot = self._plot_rect
        xmin, xmax, ymin, ymax = self._data_bounds
        x = xmin + (px - plot.left()) / max(1, plot.width()) * (xmax - xmin)
        y = ymin + (plot.bottom() - py) / max(1, plot.height()) * (ymax - ymin)
        if self.log_x:
            x = 10 ** x
        return x, y

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = (int(event.position().x()), int(event.position().y()))
            self._drag_current = self._drag_origin
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._drag_current = (int(event.position().x()), int(event.position().y()))
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_origin and self._drag_current:
            ox, oy = self._drag_origin
            cx, cy = self._drag_current
            if self._plot_rect is not None:
                plot = self._plot_rect
                ox = max(plot.left(), min(plot.right(), ox))
                cx = max(plot.left(), min(plot.right(), cx))
                oy = max(plot.top(), min(plot.bottom(), oy))
                cy = max(plot.top(), min(plot.bottom(), cy))
            dx = abs(cx - ox)
            dy = abs(cy - oy)
            if max(dx, dy) > 8:
                start = self._screen_to_data(ox, oy)
                end = self._screen_to_data(cx, cy)
                if start is not None and end is not None:
                    if dx >= dy:
                        xmin, xmax = sorted((start[0], end[0]))
                        if xmax > xmin:
                            self.x_range = (xmin, xmax)
                    else:
                        ymin, ymax = sorted((start[1], end[1]))
                        if ymax > ymin:
                            self.y_range = (ymin, ymax)
        self._drag_origin = None
        self._drag_current = None
        self.update()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.reset_zoom()
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = self.rect()
        painter.fillRect(bounds, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#d1d5db")))
        painter.drawRect(bounds.adjusted(0, 0, -1, -1))

        plot = bounds.adjusted(60, 18, -18, -40)
        painter.setPen(QPen(QColor("#e5e7eb")))
        for i in range(6):
            y = plot.top() + int(plot.height() * i / 5)
            painter.drawLine(plot.left(), y, plot.right(), y)

        painter.setPen(QPen(QColor("#6b7280")))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(plot.left(), bounds.bottom() - 12, self.x_label)
        painter.save()
        painter.translate(14, plot.center().y() + 30)
        painter.rotate(-90)
        painter.drawText(0, 0, self.y_label)
        painter.restore()

        if not self.x_values or not self.y_values:
            painter.setPen(QPen(QColor("#6b7280")))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, self.message)
            return

        xs = [math.log10(max(1e-12, x)) for x in self.x_values] if self.log_x else self.x_values
        ys = self.y_values
        if self.x_range is not None:
            xmin, xmax = self.x_range
            if self.log_x:
                xmin, xmax = math.log10(max(1e-12, xmin)), math.log10(max(1e-12, xmax))
        else:
            xmin, xmax = min(xs), max(xs)
        ymin, ymax = self.y_range if self.y_range is not None else (min(ys), max(ys))
        if xmax == xmin:
            xmax += 1.0
        if ymax == ymin:
            ymax += 1.0
        self._plot_rect = plot
        self._data_bounds = (xmin, xmax, ymin, ymax)

        painter.setPen(QPen(QColor("#64748b")))
        painter.setFont(QFont("Segoe UI", 7))
        if self.log_x:
            painter.setPen(QPen(QColor("#edf2f7")))
            for decade in (10, 100, 1000, 10000):
                for multiple in range(2, 10):
                    tick = decade * multiple
                    tx = math.log10(tick)
                    if xmin <= tx <= xmax:
                        px = plot.left() + (tx - xmin) / (xmax - xmin) * plot.width()
                        painter.drawLine(int(px), plot.top(), int(px), plot.bottom())
            ticks = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
            painter.setPen(QPen(QColor("#e5e7eb")))
            for tick in ticks:
                tx = math.log10(tick)
                if xmin <= tx <= xmax:
                    px = plot.left() + (tx - xmin) / (xmax - xmin) * plot.width()
                    painter.drawLine(int(px), plot.top(), int(px), plot.bottom())
            painter.setPen(QPen(QColor("#64748b")))
            for tick in ticks:
                tx = math.log10(tick)
                if xmin <= tx <= xmax:
                    px = plot.left() + (tx - xmin) / (xmax - xmin) * plot.width()
                    painter.drawLine(int(px), plot.bottom(), int(px), plot.bottom() + 4)
                    label = self._tick_label(tx, True)
                    label_width = painter.fontMetrics().horizontalAdvance(label)
                    label_x = int(px) - label_width // 2
                    label_x = max(plot.left() - 2, min(plot.right() - label_width + 2, label_x))
                    painter.drawText(label_x, bounds.bottom() - 22, label)
        else:
            painter.setPen(QPen(QColor("#e5e7eb")))
            for i in range(6):
                x = plot.left() + int(plot.width() * i / 5)
                painter.drawLine(x, plot.top(), x, plot.bottom())
            painter.setPen(QPen(QColor("#64748b")))
            for i in range(6):
                value = xmin + (xmax - xmin) * i / 5
                px = plot.left() + plot.width() * i / 5
                painter.drawText(int(px) - 12, bounds.bottom() - 22, self._tick_label(value, False))
        for i in range(6):
            value = ymin + (ymax - ymin) * i / 5
            py = plot.bottom() - plot.height() * i / 5
            if self.y_axis_mode == "symmetric_dbfs":
                label = self._symmetric_dbfs_label(value, max(abs(ymin), abs(ymax)))
            else:
                label = self._tick_label(value, False)
            painter.drawText(5, int(py) + 4, label)

        points = []
        for x, y in zip(xs, ys):
            px = plot.left() + (x - xmin) / (xmax - xmin) * plot.width()
            py = plot.bottom() - (y - ymin) / (ymax - ymin) * plot.height()
            points.append((px, py))

        painter.save()
        painter.setClipRect(plot)
        painter.setPen(QPen(QColor("#2563eb"), 2))
        if self.scatter:
            for (px, py), y in zip(points, ys):
                color = self._value_to_color(y, min(ys), max(ys)) if self.color_points_by_y else QColor("#2563eb")
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(int(px) - 3, int(py) - 3, 6, 6)
        else:
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))
        painter.restore()

        if self._drag_origin is not None and self._drag_current is not None:
            ox, oy = self._drag_origin
            cx, cy = self._drag_current
            ox = max(plot.left(), min(plot.right(), ox))
            cx = max(plot.left(), min(plot.right(), cx))
            oy = max(plot.top(), min(plot.bottom(), oy))
            cy = max(plot.top(), min(plot.bottom(), cy))
            overlay = QColor("#3978bd")
            overlay.setAlpha(50)
            painter.setPen(QPen(QColor("#3978bd"), 1))
            if abs(cx - ox) >= abs(cy - oy):
                left, right = sorted((ox, cx))
                painter.fillRect(left, plot.top(), max(1, right - left), plot.height(), overlay)
                painter.drawRect(left, plot.top(), max(1, right - left), plot.height())
            else:
                top, bottom = sorted((oy, cy))
                painter.fillRect(plot.left(), top, plot.width(), max(1, bottom - top), overlay)
                painter.drawRect(plot.left(), top, plot.width(), max(1, bottom - top))


class ProgressCloud(QWidget):
    """Simple 3D-ish orthographic projection for measurement progress."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.points: list[tuple[float, float, float]] = []
        self.measured_count = 0
        self.setMinimumHeight(150)

    def set_points(self, points: list[tuple[float, float, float]], measured_count: int) -> None:
        self.points = points
        self.measured_count = max(0, measured_count)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#d1d5db")))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.setPen(QPen(QColor("#111827")))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        total = len(self.points)
        painter.drawText(12, 22, f"3D Progress: {self.measured_count} / {total}")
        if not self.points:
            painter.setPen(QPen(QColor("#6b7280")))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Waiting for grid points...")
            return

        plot = rect.adjusted(18, 38, -18, -18)
        coords = [(p[0], p[1], p[2]) for p in self.points]
        max_abs = max(max(abs(v) for v in point) for point in coords) or 1.0
        scale = min(plot.width(), plot.height()) * 0.42 / max_abs
        cx, cy = plot.center().x(), plot.center().y()

        for index, (x, y, z) in enumerate(coords):
            px = cx + (y - x * 0.35) * scale
            py = cy - (z + x * 0.22) * scale
            measured = index < self.measured_count
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2563eb") if measured else QColor("#d1d5db"))
            radius = 4 if measured else 3
            painter.drawEllipse(int(px) - radius, int(py) - radius, radius * 2, radius * 2)
