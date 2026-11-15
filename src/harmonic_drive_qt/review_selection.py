"""Checkbox selection independent of the table's extended row highlighting."""
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QStyle, QStyleOptionViewItem,
                               QStyledItemDelegate, QTableWidget, QStyleFactory)

CAPTURE_COUNT_ROLE = int(Qt.ItemDataRole.UserRole)+1


class ReadOnlyCellDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Windows 11 draws an accent bar inside every selected cell. It looks
        # like a text caret; use Qt's plain item rendering for this read-only list.
        self._cell_style = QStyleFactory.create('Fusion')
        self._cell_style.setParent(self)

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        option.state &= ~QStyle.StateFlag.State_HasFocus
        self._cell_style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        count = index.data(CAPTURE_COUNT_ROLE)
        if count and count > 1:
            diameter = round(.85*max(18, option.fontMetrics.height()+4))
            badge = QRect(option.rect.right()-diameter-5, option.rect.center().y()-diameter//2,
                          diameter, diameter)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#2563eb'))
            painter.drawEllipse(badge)
            painter.setPen(QColor('white'))
            font = option.font
            font.setBold(True)
            if font.pointSizeF() > 0:
                font.setPointSizeF(font.pointSizeF()*.85)
            elif font.pixelSize() > 0:
                font.setPixelSize(max(1, round(font.pixelSize()*.85)))
            painter.setFont(font)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(count))
            painter.restore()

    def sizeHint(self, option, index):  # noqa: N802
        size = super().sizeHint(option, index)
        count = index.data(CAPTURE_COUNT_ROLE)
        if count and count > 1:
            diameter = round(.85*max(18, option.fontMetrics.height()+4))
            size.setWidth(size.width()+diameter+10)
            size.setHeight(max(size.height(), diameter+4))
        return size

    def createEditor(self, parent, option, index):  # noqa: N802
        return None


class ReviewTable(QTableWidget):
    toggle_rows = Signal(object, bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setItemDelegate(ReadOnlyCellDelegate(self))
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

    def _toggle(self, row):
        item = self.item(row, 0)
        if item is None or not item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return
        highlighted = [index.row() for index in self.selectionModel().selectedRows()]
        targets = highlighted if row in highlighted else [row]
        self.toggle_rows.emit(targets, item.checkState() != Qt.CheckState.Checked)

    def mousePressEvent(self, event):  # noqa: N802
        index = self.indexAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and index.isValid() and index.column() == 0:
            # Avoid Qt collapsing the highlighted range before the checkbox changes.
            self._toggle(index.row())
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Space and self.currentRow() >= 0:
            self._toggle(self.currentRow())
            event.accept()
            return
        super().keyPressEvent(event)
