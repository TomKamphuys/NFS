"""Review interactions: hover zoom, checkbox ranges and automatic action reports."""
import os
import json
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
from matplotlib.backend_bases import MouseEvent
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from harmonic_drive_qt.review_interaction import ReviewPlotInteraction
from nfs.review_storage import write_review_report, REVIEW_REPORT_NAME


def test_waveform_reveals_samples_only_after_zoom_settles(qtbot):
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from harmonic_drive_qt.review_waveform import AdaptiveWaveform
    figure = Figure(figsize=(8, 5))
    canvas = FigureCanvasQTAgg(figure)
    qtbot.addWidget(canvas)
    ax = figure.subplots()
    signal = np.sin(np.arange(500000)*.1)
    waveform = AdaptiveWaveform(ax, signal, 100000)
    canvas.show()
    canvas.draw()
    qtbot.waitUntil(lambda: waveform._view[2] == max(64, int(ax.bbox.width)), timeout=2000)
    assert waveform.envelope is not None
    original_envelope = waveform.envelope
    calls = []
    update = waveform.update

    def tracked_update(*args):
        calls.append(True)
        return update(*args)

    waveform.update = tracked_update
    ax.set_xlim(1.50, 1.59)
    canvas.draw()
    qtbot.wait(80)
    ax.set_xlim(1.51, 1.58)
    canvas.draw()
    qtbot.wait(80)
    assert not calls
    assert waveform.envelope is original_envelope
    qtbot.waitUntil(lambda: waveform.envelope is None, timeout=2000)
    assert len(calls) == 1
    first = int(np.floor(1.51*100000))-1
    last = int(np.ceil(1.58*100000))+2
    np.testing.assert_array_equal(waveform.line.get_ydata(), signal[first:last])
    np.testing.assert_allclose(waveform.line.get_xdata(), np.arange(first, last)/100000)


@pytest.mark.parametrize('scale', ['linear', 'log'])
def test_middle_drag_pans_only_grabbed_plot_and_reset_restores_it(scale):
    figure = Figure(figsize=(9, 5))
    canvas = FigureCanvasAgg(figure)
    ax, other = figure.subplots(1, 2)
    ax.set(xscale=scale, xlim=(1, 100), ylim=(0, 10))
    interaction = ReviewPlotInteraction(canvas)
    interaction.set_axes((ax, other))
    canvas.draw()
    initial = ax.get_xlim(), ax.get_ylim()
    other_initial = other.get_xlim(), other.get_ylim()
    x, y = ax.bbox.x0+ax.bbox.width*.5, ax.bbox.y0+ax.bbox.height*.5
    press = MouseEvent('button_press_event', canvas, x, y, button=2)
    anchor = ax.transData.inverted().transform((press.x, press.y))
    interaction.click(press)
    motion = MouseEvent('motion_notify_event', canvas, press.x+40, press.y+20, button=2)
    interaction.motion(motion)
    np.testing.assert_allclose(ax.transData.transform(anchor), (motion.x, motion.y))
    assert (ax.get_xlim(), ax.get_ylim()) != initial
    assert (other.get_xlim(), other.get_ylim()) == other_initial
    if scale == 'log':
        assert ax.get_xlim()[1]/ax.get_xlim()[0] == pytest.approx(initial[0][1]/initial[0][0])
    else:
        assert ax.get_xlim()[1]-ax.get_xlim()[0] == pytest.approx(initial[0][1]-initial[0][0])
    interaction.release(MouseEvent('button_release_event', canvas, motion.x, motion.y, button=2))
    final = ax.get_xlim(), ax.get_ylim()
    interaction.motion(MouseEvent('motion_notify_event', canvas, x+80, y+50))
    assert (ax.get_xlim(), ax.get_ylim()) == final
    interaction.reset(ax)
    np.testing.assert_allclose(ax.get_xlim(), initial[0])
    np.testing.assert_allclose(ax.get_ylim(), initial[1])


def test_middle_drag_over_time_axis_only_pans_time():
    figure = Figure(figsize=(7, 5))
    canvas = FigureCanvasAgg(figure)
    ax = figure.subplots()
    ax.plot([0, 10], [0, 10])
    interaction = ReviewPlotInteraction(canvas)
    interaction.set_axes((ax,))
    canvas.draw()
    initial = ax.get_xlim(), ax.get_ylim()
    x, y = ax.bbox.x0+ax.bbox.width*.5, ax.bbox.y0-12
    interaction.click(MouseEvent('button_press_event', canvas, x, y, button=2))
    interaction.motion(MouseEvent('motion_notify_event', canvas, x+30, y+30, button=2))
    assert ax.get_xlim() != initial[0]
    assert ax.get_ylim() == initial[1]
    interaction.clear()
    assert interaction._pan is None


def test_wheel_zoom_preserves_hover_anchor_on_log_plot_and_resets_one_plot():
    figure = Figure(figsize=(8, 5))
    canvas = FigureCanvasAgg(figure)
    ax, other = figure.subplots(1, 2)
    ax.semilogx([1, 10, 100], [0, 5, 10])
    other.plot([0, 1], [0, 1])
    interaction = ReviewPlotInteraction(canvas)
    interaction.set_axes((ax, other))
    canvas.draw()
    initial = ax.get_xlim(), ax.get_ylim()
    other_initial = other.get_xlim(), other.get_ylim()
    x, y = ax.transData.transform((10, 5))
    event = MouseEvent('scroll_event', canvas, x, y, step=1)
    before = ax.transAxes.inverted().transform((event.x, event.y))
    anchor = ax.transData.inverted().transform((event.x, event.y))
    interaction.scroll(event)
    after = ax.transAxes.inverted().transform(ax.transData.transform(anchor))
    np.testing.assert_allclose(after, before)
    assert np.log10(ax.get_xlim()[1]/ax.get_xlim()[0]) < np.log10(initial[0][1]/initial[0][0])
    assert ax.get_ylim()[1]-ax.get_ylim()[0] < initial[1][1]-initial[1][0]
    assert (other.get_xlim(), other.get_ylim()) == other_initial
    canvas.draw()
    bbox = interaction.reset_buttons[ax].get_window_extent(canvas.get_renderer())
    interaction.click(MouseEvent('button_press_event', canvas, *bbox.get_points().mean(axis=0), button=1))
    np.testing.assert_allclose(ax.get_xlim(), initial[0])
    np.testing.assert_allclose(ax.get_ylim(), initial[1])


@pytest.mark.parametrize('dimension', ['x', 'y'])
def test_hovering_axis_ticks_zooms_only_that_axis(dimension):
    figure = Figure(figsize=(7, 5))
    canvas = FigureCanvasAgg(figure)
    ax = figure.subplots()
    ax.plot([0, 10], [-5, 5])
    ax.set(xlabel='Time (s)', ylabel='Amplitude')
    interaction = ReviewPlotInteraction(canvas)
    interaction.set_axes((ax,))
    canvas.draw()
    initial = ax.get_xlim(), ax.get_ylim()
    if dimension == 'x':
        x, y = ax.bbox.x0+ax.bbox.width*.35, ax.bbox.y0-12
    else:
        x, y = ax.bbox.x0-12, ax.bbox.y0+ax.bbox.height*.35
    event = MouseEvent('scroll_event', canvas, x, y, step=1)
    assert interaction.target(event) == (ax, dimension)
    interaction.scroll(event)
    assert (ax.get_ylim() if dimension == 'x' else ax.get_xlim()) == initial[1 if dimension == 'x' else 0]
    assert (ax.get_xlim() if dimension == 'x' else ax.get_ylim()) != initial[0 if dimension == 'x' else 1]


def test_hover_zoom_passes_application_wheel_guard_without_clicking(qtbot):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from harmonic_drive_qt.wheel_guard import WheelGuard
    figure = Figure(figsize=(7, 5))
    canvas = FigureCanvasQTAgg(figure)
    canvas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    qtbot.addWidget(canvas)
    ax = figure.subplots()
    ax.plot([0, 10], [0, 10])
    interaction = ReviewPlotInteraction(canvas)
    interaction.set_axes((ax,))
    guard = WheelGuard(canvas)
    canvas.installEventFilter(guard)
    canvas.show()
    canvas.draw()
    assert not canvas.hasFocus()
    before = ax.get_xlim()
    x, y = ax.transData.transform((5, 5))
    ratio = canvas.device_pixel_ratio
    pos = QPointF(x/ratio, canvas.height()-y/ratio)
    event = QWheelEvent(pos, pos, QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(canvas, event)
    assert ax.get_xlim() == before  # Ordinary canvases still require focus.
    canvas.setProperty('hoverWheelZoom', True)
    QApplication.sendEvent(canvas, event)
    assert ax.get_xlim()[1]-ax.get_xlim()[0] < before[1]-before[0]


def test_report_preserves_reason_and_exclusion_status_across_rescans(tmp_path):
    rows = [dict(filename='bad_ir.wav', status='repeat', reason='LF response differs.'),
            dict(filename='good_ir.wav', status='pass', reason='')]
    report = write_review_report(tmp_path, rows)
    assert report.name == REVIEW_REPORT_NAME
    document = json.loads(report.read_text(encoding='utf-8'))
    assert document['files'] == [dict(Filename='bad_ir.wav', Status='Pending review', Finding='Suspect', Reason='LF response differs.')]
    write_review_report(tmp_path, rows, {'bad_ir.wav': 'Excluded'})
    write_review_report(tmp_path, [dict(filename='bad_ir.wav.blacklisted', status='excluded', reason='Renamed')])
    entry = json.loads(report.read_text(encoding='utf-8'))['files'][0]
    assert entry == dict(Filename='bad_ir.wav', Status='Excluded', Finding='Suspect', Reason='LF response differs.')
    write_review_report(tmp_path, rows, {'bad_ir.wav': 'Restored'})
    write_review_report(tmp_path, [dict(filename='bad_ir.wav', status='pass', reason='')])
    entry = json.loads(report.read_text(encoding='utf-8'))['files'][0]
    assert entry == dict(Filename='bad_ir.wav', Status='Restored', Finding='Okay', Reason='LF response differs.')
    assert not list(tmp_path.glob('*.tmp'))


def test_mic_plot_has_sweep_and_analysis_regions_without_loopback(monkeypatch, tmp_path):
    from harmonic_drive_qt import review_plots
    figure = Figure(figsize=(12, 7))
    FigureCanvasAgg(figure)

    def audio(path):
        if path.name.endswith('_capture.wav'):
            return 1000, np.column_stack((np.ones(1000), np.sin(np.arange(1000))))
        return 1000, np.sin(np.arange(1000))

    monkeypatch.setattr(review_plots, 'read_audio', audio)
    row = dict(filename='point_ir.wav', metrics=dict(sweep_regions_s=[[.2, .8]],
               pre_start_s=.02, pre_end_s=.18, tail_start_s=.85, tail_end_s=.98))
    axes = review_plots.draw_inspection(figure, tmp_path, row, [row])
    assert len(axes) == 3
    assert all(ax.get_subplotspec().get_gridspec().ncols == 1 for ax in axes)
    assert [ax.get_subplotspec().rowspan.start for ax in axes] == [0, 1, 2]
    assert axes[0].get_title() == 'Microphone capture'
    assert not any('loopback' in ax.get_title().lower() for ax in axes)
    assert not any('IR' in ax.get_title() for ax in axes)
    assert [patch.get_label() for patch in axes[0].patches] == [
        'Sweep', 'Pre-sweep analysis', 'Post-sweep analysis']
    interaction = ReviewPlotInteraction(figure.canvas)
    interaction.set_axes(axes)
    assert len(interaction.reset_buttons) == 3


def test_shift_highlight_and_checkbox_toggle_range_and_header(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from harmonic_drive_qt import project, measurement_review as module

    class Pool:
        workers = 1
        def warm(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(module, 'ReviewProcessPool', Pool)
    monkeypatch.setattr(project, 'get_project_dir', lambda: tmp_path)
    monkeypatch.setattr(module, 'draw_inspection', lambda figure, *args: (figure.subplots(),))
    pane = module.MeasurementReviewPane(SimpleNamespace(is_measurement_set_running=lambda: False))
    qtbot.addWidget(pane)
    pane.rows = [dict(filename=f'point_{i}_ir.wav', status='repeat', marked=False, score=2,
                      reason='Needs review') for i in range(5)]
    pane.populate()
    pane.resize(1650, 950)
    pane.show()
    qtbot.waitExposed(pane)
    assert pane.table.columnCount() == 5
    assert [pane.table.horizontalHeaderItem(i).text() for i in range(5)] == [
        'Select', 'Filename', 'Issue description', 'LF band (Hz)', 'Chosen capture']
    assert pane.list_panel.x() < pane.plot_panel.x()
    from PySide6.QtWidgets import QHeaderView
    assert all(pane.table.horizontalHeader().sectionResizeMode(i) == QHeaderView.ResizeMode.Fixed
               for i in range(4))
    assert not pane.table.wordWrap()
    qtbot.waitUntil(lambda: pane.list_panel.width() > 260)
    list_width, plot_width = pane.list_panel.width(), pane.plot_panel.width()
    pane.resize(pane.width()+400, pane.height())
    qtbot.waitUntil(lambda: pane.plot_panel.width() > plot_width+300)
    assert abs(pane.list_panel.width()-list_width) <= 2
    pane.resize(pane.width()+200, pane.height())
    qtbot.wait(20)
    assert abs(pane.list_panel.width()-list_width) <= 2
    cell = lambda row, column: pane.table.visualItemRect(pane.table.item(row, column)).center()
    qtbot.mouseClick(pane.table.viewport(), Qt.MouseButton.LeftButton, pos=cell(1, 1))
    qtbot.mouseClick(pane.table.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ShiftModifier, cell(3, 1))
    assert [i.row() for i in pane.table.selectionModel().selectedRows()] == [1, 2, 3]
    qtbot.mouseClick(pane.table.viewport(), Qt.MouseButton.LeftButton, pos=cell(2, 0))
    assert [row['marked'] for row in pane.rows] == [False, True, True, True, False]
    assert [i.row() for i in pane.table.selectionModel().selectedRows()] == [1, 2, 3]
    assert pane.exclude_button.text() == 'Exclude (3)'
    assert pane.recapture_button.text() == 'Re-capture (3)'
    assert pane.recapture_button.isEnabled()
    qtbot.mouseClick(pane.table.viewport(), Qt.MouseButton.LeftButton, pos=cell(1, 0))
    assert not any(row['marked'] for row in pane.rows)
    qtbot.mouseClick(pane.select_header.viewport(), Qt.MouseButton.LeftButton,
                     pos=QPoint(12, pane.select_header.height()//2))
    assert not any(row['marked'] for row in pane.rows)
    # Bulk selection helpers preserve hidden selections.
    pane.rows[0]['status'] = 'pass'
    pane.rows[0]['marked'] = True
    pane.populate()
    pane._check_all_visible(True)
    pane._check_all_visible(False)
    assert pane.rows[0]['marked']
    assert not any(row['marked'] for row in pane.rows[1:])
    pane.shutdown()


def test_readonly_review_cells_keep_selection_without_editors(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractItemView, QLineEdit, QTableWidgetItem
    from harmonic_drive_qt.review_selection import ReviewTable
    table = ReviewTable(2, 3)
    qtbot.addWidget(table)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
    for row in range(2):
        for column in range(3):
            table.setItem(row, column, QTableWidgetItem(f'File {row} field {column}'))
    table.show()
    for column in (1, 2):
        item = table.item(0, column)
        qtbot.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(item).center())
        qtbot.keyClick(table, Qt.Key.Key_F2)
        qtbot.keyClicks(table, 'cannot edit')
        assert not table.findChildren(QLineEdit)
        assert item.text() == f'File 0 field {column}'
        assert table.selectionModel().selectedRows()[0].row() == 0
    assert table.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor
