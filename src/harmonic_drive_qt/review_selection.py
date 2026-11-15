"""Checkbox selection independent of the table's extended row highlighting."""
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtWidgets import QHeaderView, QStyle, QStyleOptionButton, QTableWidget


class SelectHeader(QHeaderView):
    toggle_all = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.check_state = Qt.CheckState.Unchecked
        self.has_rows = False

    def paintSection(self, painter, rect, logical_index):  # noqa: N802
        super().paintSection(painter, rect, logical_index)
        if logical_index == 0:
            option = QStyleOptionButton()
            option.rect = QRect(rect.left()+5, rect.center().y()-7, 15, 15)
            option.state = QStyle.StateFlag.State_Enabled if self.isEnabled() and self.has_rows else QStyle.StateFlag.State_None
            option.state |= {Qt.CheckState.Checked: QStyle.StateFlag.State_On,
                             Qt.CheckState.PartiallyChecked: QStyle.StateFlag.State_NoChange,
                             Qt.CheckState.Unchecked: QStyle.StateFlag.State_Off}[self.check_state]
            self.style().drawControl(QStyle.ControlElement.CE_CheckBox, option, painter, self)

    def mousePressEvent(self, event):  # noqa: N802
        if (event.button() == Qt.MouseButton.LeftButton and self.logicalIndexAt(event.position().toPoint()) == 0
                and event.position().x() < self.sectionViewportPosition(0)+self.sectionSize(0)-4):
            if self.has_rows:
                self.toggle_all.emit(self.check_state != Qt.CheckState.Checked)
            event.accept()
            return
        super().mousePressEvent(event)


class ReviewTable(QTableWidget):
    toggle_rows = Signal(object, bool)

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
