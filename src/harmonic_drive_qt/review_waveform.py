"""Waveform detail chosen from the visible samples, rather than a fixed envelope."""
import numpy as np


class AdaptiveWaveform:
    def __init__(self, ax, signal, fs):
        if signal.ndim != 1 or not len(signal):
            raise ValueError('Expected a non-empty mono signal')
        self.ax, self.signal, self.fs = ax, signal, fs
        self.line, = ax.plot([], [], color='tab:blue', linewidth=.8)
        self.envelope = None
        self._view = None
        self._updating = False
        self._timer = ax.figure.canvas.new_timer(interval=150)
        self._timer.single_shot = True
        self._timer.add_callback(self._settled_update)
        # Set initial limits from all samples; later detail updates must not autoscale.
        ax.update_datalim([[0, float(np.min(signal))],
                           [(len(signal)-1)/fs, float(np.max(signal))]])
        ax.autoscale_view()
        ax.callbacks.connect('xlim_changed', self.request_update)
        ax.figure.canvas.mpl_connect('draw_event', self._drawn)
        self.update()

    def _drawn(self, event):
        # Constrained layout and window resizing can change the available pixel width.
        if self._view is not None and max(64, int(self.ax.bbox.width)) != self._view[2]:
            self.request_update()

    def request_update(self, *_args):
        self._timer.stop()
        self._timer.start()

    def _settled_update(self):
        if self.update():
            self.ax.figure.canvas.draw_idle()

    def update(self, *_args):
        if self._updating or self.ax not in self.ax.figure.axes:
            return False
        left, right = sorted(self.ax.get_xlim())
        first = max(0, min(len(self.signal), int(np.floor(left*self.fs))-1))
        last = max(0, min(len(self.signal), int(np.ceil(right*self.fs))+2))
        pixels = max(64, int(self.ax.bbox.width))
        view = first, last, pixels
        if view == self._view:
            return False
        self._view = view
        self._updating = True
        try:
            if self.envelope is not None:
                self.envelope.remove()
                self.envelope = None
            visible = self.signal[first:last]
            if len(visible) <= max(10000, pixels*8):
                self.line.set_data(np.arange(first, last)/self.fs, visible)
            else:
                # One extrema block per horizontal pixel, including the final partial block.
                size = max(1, int(np.ceil(len(visible)/pixels)))
                starts = np.arange(0, len(visible), size)
                low = np.minimum.reduceat(visible, starts)
                high = np.maximum.reduceat(visible, starts)
                centres = first + (starts+np.minimum(starts+size, len(visible))-1)/2
                self.line.set_data([], [])
                xlim, ylim = self.ax.get_xlim(), self.ax.get_ylim()
                self.envelope = self.ax.fill_between(centres/self.fs, low, high,
                                                     color='tab:blue', alpha=.7)
                self.ax.set_xlim(xlim, emit=False)
                self.ax.set_ylim(ylim, emit=False)
            return True
        finally:
            self._updating = False
