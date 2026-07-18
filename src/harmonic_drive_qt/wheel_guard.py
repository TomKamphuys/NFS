"""Application-wide guard against wheel-scroll hijacking.

Widgets that consume mouse-wheel events (combo boxes, spin boxes, sliders and
the embedded 3D viewers) would otherwise change their value / zoom the scene
whenever the pointer merely hovers over them while the user scrolls the
surrounding page.

Two policies are applied:

* Value-entry fields (combo boxes and spin boxes) never react to the wheel at
  all - their value can only be changed by typing (or, for combo boxes, by
  opening the popup and clicking). The wheel event is always forwarded to the
  enclosing scroll area so the page scrolls.
* Sliders and the embedded 3D viewers only react to the wheel when they have
  keyboard focus (i.e. after the user explicitly clicks them); otherwise the
  wheel scrolls the page.
"""

from __future__ import annotations

from .qt_compat import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QEvent,
    QObject,
    QScrollArea,
    QSlider,
)

# Value-entry widgets: the wheel is disabled entirely, only typing changes them.
_ALWAYS_BLOCKED_TYPES = (QComboBox, QAbstractSpinBox)

# Widgets that steal wheel scrolling but should still respond when focused.
_FOCUS_GUARDED_TYPES = (QSlider,)

# Class-name hints for widgets that cannot be imported cheaply here (the
# matplotlib canvas and the PyVista/VTK 3D interactor). Matching on the class
# name keeps this module free of heavy optional dependencies.
_FOCUS_GUARDED_NAME_HINTS = ("FigureCanvas", "Interactor", "RenderWindow")


class WheelGuard(QObject):
    """Event filter that blocks wheel events on scroll-stealing widgets."""

    def _is_focus_guarded(self, obj) -> bool:
        if isinstance(obj, _FOCUS_GUARDED_TYPES):
            return True
        name = type(obj).__name__
        return any(hint in name for hint in _FOCUS_GUARDED_NAME_HINTS)

    @staticmethod
    def _enclosing_scroll_area(obj):
        """Return the nearest QScrollArea ancestor of ``obj`` (or ``None``)."""
        parent = obj.parentWidget() if hasattr(obj, "parentWidget") else None
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    @staticmethod
    def _scroll_by_wheel(area, event) -> bool:
        """Scroll ``area`` vertically according to ``event``'s wheel delta.

        Re-dispatching the original wheel event to the scroll area's viewport
        works for ordinary widgets but not for the native VTK 3D interactor,
        whose OpenGL window swallows the re-sent event and leaves the page
        stuck. Moving the scrollbar value directly is backend agnostic and
        keeps the surrounding page scrolling in every case.
        """
        bar = area.verticalScrollBar()
        if bar is None:
            return False
        pixel = event.pixelDelta()
        if not pixel.isNull() and pixel.y() != 0:
            bar.setValue(bar.value() - pixel.y())
            return True
        delta = event.angleDelta().y()
        if delta == 0:
            return False
        # A wheel notch is 120 units; scroll the platform's configured number
        # of lines, falling back to a sensible pixel step per line.
        lines = QApplication.wheelScrollLines() or 3
        step = bar.singleStep() or 20
        bar.setValue(bar.value() - int(round(delta / 120.0 * lines * step)))
        return True

    def _redirect_to_scroll_area(self, obj, event) -> bool:
        """Forward a blocked wheel event to the enclosing scroll area.

        Some widgets (notably the native VTK 3D interactor) consume wheel
        events without letting them propagate to the parent, so merely
        ignoring the event leaves the surrounding page unable to scroll.
        Scrolling the enclosing scroll area directly keeps the page moving
        when the pointer hovers over such widgets.
        """
        area = self._enclosing_scroll_area(obj)
        if area is not None and self._scroll_by_wheel(area, event):
            event.accept()
            return True
        event.ignore()
        return True

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            if isinstance(obj, _ALWAYS_BLOCKED_TYPES):
                return self._redirect_to_scroll_area(obj, event)
            if (
                self._is_focus_guarded(obj)
                and hasattr(obj, "hasFocus")
                and not obj.hasFocus()
            ):
                return self._redirect_to_scroll_area(obj, event)
        return super().eventFilter(obj, event)
