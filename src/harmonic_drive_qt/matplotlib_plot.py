"""Matplotlib-backed plots used by the Live Capture pane."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.ticker import FixedLocator, FuncFormatter, LogLocator, NullFormatter
from matplotlib.widgets import RectangleSelector


_POSITION_CMAP = LinearSegmentedColormap.from_list(
    "measurement_elevation",
    ("#2563eb", "#14b8a6", "#f472b6"),
)


class MatplotlibPlot(FigureCanvasQTAgg):
    """A line/scatter canvas with box zoom and right-button panning."""

    def __init__(self, title: str, x_label: str = "", y_label: str = "", parent=None) -> None:
        figure = Figure(facecolor="white")
        self.axes = figure.add_subplot(111)
        super().__init__(figure)
        if parent is not None:
            self.setParent(parent)

        self.title = title
        self.x_label = x_label
        self.y_label = y_label
        self.scatter = False
        self.log_x = False
        self.color_points_by_y = False
        self.y_axis_mode = "linear"
        self.x_range: tuple[float, float] | None = None
        self.y_range: tuple[float, float] | None = None
        self.default_x_range: tuple[float, float] | None = None
        self.default_y_range: tuple[float, float] | None = None
        self._x_values = np.array([], dtype=float)
        self._y_values = np.array([], dtype=float)
        self._message = "Waiting for data..."
        self._selector: RectangleSelector | None = None
        self._pan_start: tuple[
            float,
            float,
            tuple[float, float],
            tuple[float, float],
        ] | None = None

        self.figure.subplots_adjust(left=0.10, right=0.975, top=0.965, bottom=0.17)
        self.setMinimumHeight(130)
        self.mpl_connect("button_press_event", self._on_button_press)
        self.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.mpl_connect("button_release_event", self._on_button_release)
        self._render()

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
        self._pan_start = None
        self._x_values = np.asarray(list(x_values), dtype=float)
        self._y_values = np.asarray(list(y_values), dtype=float)
        if title is not None:
            self.title = title
        self.scatter = scatter
        self.log_x = log_x
        self.x_range = x_range
        self.y_range = y_range
        self.y_axis_mode = y_axis_mode or "linear"
        self._message = ""
        self._render()
        self.default_x_range = tuple(float(value) for value in self.axes.get_xlim())
        self.default_y_range = tuple(float(value) for value in self.axes.get_ylim())

    def clear_data(self, message: str, title: str | None = None) -> None:
        self._pan_start = None
        self._x_values = np.array([], dtype=float)
        self._y_values = np.array([], dtype=float)
        self._message = message
        if title is not None:
            self.title = title
        self._render()

    def reset_zoom(self) -> None:
        if self._x_values.size == 0 or self._y_values.size == 0:
            return
        self._pan_start = None
        self.x_range = self.default_x_range
        self.y_range = self.default_y_range
        self._apply_limits()
        self.draw_idle()

    def _style_axes(self) -> None:
        axes = self.axes
        axes.set_facecolor("white")
        axes.set_xlabel(self.x_label, color="#6b7280", fontsize=8)
        axes.set_ylabel(self.y_label, color="#6b7280", fontsize=8)
        axes.tick_params(axis="both", colors="#64748b", labelsize=7)
        axes.grid(True, which="major", color="#e5e7eb", linewidth=0.8)
        if self.log_x:
            axes.xaxis.set_major_locator(FixedLocator((
                20, 50, 100, 200, 500, 1000, 2000, 5000,
                10000, 20000, 50000, 100000, 200000,
            )))
            axes.xaxis.set_major_formatter(FuncFormatter(self._audio_frequency_label))
            axes.xaxis.set_minor_locator(LogLocator(base=10, subs=(0.3, 0.4, 0.6, 0.7, 0.8, 0.9)))
            axes.xaxis.set_minor_formatter(NullFormatter())
            axes.grid(True, which="minor", axis="x", color="#edf2f7", linewidth=0.6)
        for spine in axes.spines.values():
            spine.set_color("#d1d5db")

    def _render(self) -> None:
        self.axes.clear()
        if self.log_x:
            positive_x = self._x_values[self._x_values > 0]
            if self.x_range is not None:
                self.axes.set_xlim(*self.x_range)
            elif positive_x.size:
                self.axes.set_xlim(float(positive_x.min()), float(positive_x.max()))
        self.axes.set_xscale("log" if self.log_x else "linear")
        self._style_axes()

        if self._x_values.size == 0 or self._y_values.size == 0:
            self.axes.text(
                0.5,
                0.5,
                self._message,
                transform=self.axes.transAxes,
                ha="center",
                va="center",
                color="#6b7280",
                fontsize=8,
            )
            self.axes.set_xticks([])
            self.axes.set_yticks([])
        elif self.scatter:
            colors = self._y_values if self.color_points_by_y else "#2563eb"
            self.axes.scatter(
                self._x_values,
                self._y_values,
                c=colors,
                cmap=_POSITION_CMAP if self.color_points_by_y else None,
                s=28,
                edgecolors="none",
            )
            self._apply_limits()
        else:
            self.axes.plot(self._x_values, self._y_values, color="#2563eb", linewidth=1.6)
            self._apply_limits()

        if self.y_axis_mode == "symmetric_dbfs":
            self.axes.yaxis.set_major_formatter(FuncFormatter(self._symmetric_dbfs_label))
        self._replace_selector()
        self.draw_idle()

    def _replace_selector(self) -> None:
        if self._selector is not None:
            self._selector.disconnect_events()
        self._selector = RectangleSelector(
            self.axes,
            self._apply_selection,
            useblit=True,
            button=[1],
            minspanx=0,
            minspany=0,
            spancoords="pixels",
            interactive=False,
            props={"facecolor": "#3978bd", "edgecolor": "#3978bd", "alpha": 0.20},
        )

    def _apply_limits(self) -> None:
        if self.x_range is not None:
            self.axes.set_xlim(*self.x_range)
        else:
            self.axes.relim()
            self.axes.autoscale_view(scalex=True, scaley=False)
        if self.y_range is not None:
            self.axes.set_ylim(*self.y_range)
        else:
            self.axes.relim()
            self.axes.autoscale_view(scalex=False, scaley=True)

    def _apply_selection(self, press, release) -> None:
        if None in (press.xdata, press.ydata, release.xdata, release.ydata):
            return
        dx = abs(float(release.x) - float(press.x))
        dy = abs(float(release.y) - float(press.y))
        if max(dx, dy) <= 8:
            return
        xmin, xmax = sorted((float(press.xdata), float(release.xdata)))
        ymin, ymax = sorted((float(press.ydata), float(release.ydata)))
        if xmax > xmin and ymax > ymin:
            self.x_range = (xmin, xmax)
            self.y_range = (ymin, ymax)
            self.axes.set_xlim(xmin, xmax)
            self.axes.set_ylim(ymin, ymax)
        self.draw_idle()

    def _on_button_press(self, event) -> None:
        if event.button == 1 and event.dblclick:
            self.reset_zoom()
        elif event.button == 3 and event.inaxes is self.axes:
            self._pan_start = (
                float(event.x),
                float(event.y),
                tuple(float(value) for value in self.axes.get_xlim()),
                tuple(float(value) for value in self.axes.get_ylim()),
            )

    def _on_mouse_move(self, event) -> None:
        if self._pan_start is None or event.x is None or event.y is None:
            return
        start_x, start_y, initial_x, initial_y = self._pan_start
        axes_width = max(float(self.axes.bbox.width), 1.0)
        axes_height = max(float(self.axes.bbox.height), 1.0)
        dx_fraction = (float(event.x) - start_x) / axes_width
        dy_fraction = (float(event.y) - start_y) / axes_height

        if self.log_x:
            transformed_x = np.log10(np.asarray(initial_x, dtype=float))
            transformed_x -= dx_fraction * (transformed_x[1] - transformed_x[0])
            new_x = tuple(float(value) for value in np.power(10.0, transformed_x))
        else:
            x_span = initial_x[1] - initial_x[0]
            new_x = (
                initial_x[0] - dx_fraction * x_span,
                initial_x[1] - dx_fraction * x_span,
            )
        y_span = initial_y[1] - initial_y[0]
        new_y = (
            initial_y[0] - dy_fraction * y_span,
            initial_y[1] - dy_fraction * y_span,
        )
        self.x_range = new_x
        self.y_range = new_y
        self.axes.set_xlim(*new_x)
        self.axes.set_ylim(*new_y)
        self.draw_idle()

    def _on_button_release(self, event) -> None:
        if event.button == 3:
            self._pan_start = None

    def _symmetric_dbfs_label(self, value: float, _position: int) -> str:
        ymin, ymax = self.axes.get_ylim()
        ymax_abs = max(abs(ymin), abs(ymax), 1e-12)
        magnitude = abs(value) / ymax_abs
        if magnitude <= 1e-6:
            return "-inf"
        db = 20.0 * np.log10(min(1.0, magnitude))
        if abs(db) < 0.05:
            return "0"
        if db <= -10:
            return f"{db:.0f}"
        return f"{db:.1f}".rstrip("0").rstrip(".")

    @staticmethod
    def _audio_frequency_label(value: float, _position: int) -> str:
        if value >= 1000:
            return f"{value / 1000:g}k"
        return f"{value:g}"
