"""Mouse-centred zoom, middle-button pan and per-plot resets."""
import numpy as np
from matplotlib.backend_bases import cursors
from matplotlib.transforms import Bbox


class ReviewPlotInteraction:
    def __init__(self, canvas):
        self.canvas = canvas
        self.defaults = {}
        self.reset_buttons = {}
        self._pan = None
        canvas.mpl_connect('scroll_event', self.scroll)
        canvas.mpl_connect('button_press_event', self.click)
        canvas.mpl_connect('motion_notify_event', self.motion)
        canvas.mpl_connect('button_release_event', self.release)
        canvas.mpl_connect('figure_leave_event', self.release)

    def set_axes(self, axes):
        self.release()
        self.defaults = {ax: (ax.get_xlim(), ax.get_ylim()) for ax in axes}
        self.reset_buttons = {
            ax: ax.text(.985, .98, 'Reset axes', transform=ax.transAxes, ha='right', va='top',
                        fontsize=8, color='#1d4ed8', zorder=20,
                        bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#94a3b8', alpha=.95))
            for ax in axes}

    def clear(self):
        self.release()
        self.defaults.clear()
        self.reset_buttons.clear()

    def reset(self, ax):
        xlim, ylim = self.defaults[ax]
        ax.set_xlim(xlim)
        ax.set_ylim(ylim)
        self.canvas.draw_idle()

    def click(self, event):
        if event.button == 2:
            ax, dimension = self.target(event)
            if ax is not None:
                self.release()
                ax.start_pan(event.x, event.y, 1)
                self._pan = ax, dimension
                self.canvas.set_cursor(cursors.MOVE)
            return
        if event.button != 1:
            return
        for ax, button in self.reset_buttons.items():
            if button.contains(event)[0]:
                self.reset(ax)
                return

    def motion(self, event):
        if self._pan is not None and event.x is not None and event.y is not None:
            ax, dimension = self._pan
            ax.drag_pan(1, dimension if dimension in ('x', 'y') else None, event.x, event.y)
            self.canvas.draw_idle()

    def release(self, event=None):
        if self._pan is not None:
            ax, _ = self._pan
            ax.end_pan()
            self._pan = None
            self.canvas.set_cursor(cursors.POINTER)

    def target(self, event):
        """The plot body zooms both axes; tick/label/spine regions zoom one."""
        if event.inaxes in self.defaults:
            ax = event.inaxes
            if abs(event.y-ax.bbox.y0) <= 5:
                return ax, 'x'
            if abs(event.x-ax.bbox.x0) <= 5:
                return ax, 'y'
            return ax, 'xy'
        renderer = self.canvas.get_renderer()
        candidates = []
        for ax in self.defaults:
            xb = ax.xaxis.get_tightbbox(renderer)
            yb = ax.yaxis.get_tightbbox(renderer)
            if xb is not None:
                region = Bbox.from_extents(min(xb.x0, ax.bbox.x0), xb.y0-3,
                                           max(xb.x1, ax.bbox.x1), ax.bbox.y0+5)
                if region.contains(event.x, event.y):
                    candidates.append((abs(event.y-ax.bbox.y0), ax, 'x'))
            if yb is not None:
                region = Bbox.from_extents(yb.x0-3, min(yb.y0, ax.bbox.y0),
                                           ax.bbox.x0+5, max(yb.y1, ax.bbox.y1))
                if region.contains(event.x, event.y):
                    candidates.append((abs(event.x-ax.bbox.x0), ax, 'y'))
        if candidates:
            _, ax, dimension = min(candidates, key=lambda item: item[0])
            return ax, dimension
        return None, None

    @staticmethod
    def _limits(axis, limits, anchor, factor):
        # Zoom in transformed coordinates so log-frequency plots behave correctly.
        transform = axis.get_transform()
        lo, hi = transform.transform(np.asarray(limits))
        centre = transform.transform(np.asarray([anchor]))[0]
        result = transform.inverted().transform(centre+(np.array([lo, hi])-centre)*factor)
        return result if np.isfinite(result).all() and result[0] != result[1] else limits

    def scroll(self, event):
        if not event.step:
            return
        ax, dimension = self.target(event)
        if ax is None:
            return
        anchor = ax.transData.inverted().transform((event.x, event.y))
        factor = 1.2**(-np.clip(event.step, -10, 10))
        if 'x' in dimension:
            ax.set_xlim(self._limits(ax.xaxis, ax.get_xlim(), anchor[0], factor))
        if 'y' in dimension:
            ax.set_ylim(self._limits(ax.yaxis, ax.get_ylim(), anchor[1], factor))
        self.canvas.draw_idle()
