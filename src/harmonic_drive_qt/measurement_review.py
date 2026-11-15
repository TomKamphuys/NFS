"""Full-width native measurement review, with cancellable background analysis."""
import queue
import threading

from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidgetItem
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from nfs.measurement_review import scan_directory, rename_reviewed
from nfs.review_pool import ReviewProcessPool
from nfs.review_storage import (write_review_report, load_review_results, dataset_identity, analysis_deletion_plan,
                                delete_analysis_data)
from . import project
from .qt_compat import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
                        QProgressBar, QTimer, QMessageBox, Qt)
from .review_plots import draw_inspection
from .review_interaction import ReviewPlotInteraction
from .review_selection import ReviewTable, SelectHeader


def short_issue_description(row):
    labels = {
        'pre_sweep_noise': 'Pre-sweep noise',
        'late_noise': 'Post-sweep noise',
        'spatial_discrepancy': 'LF response outside prediction',
        'clipping': 'Microphone clipping',
        'silent_channel': 'Silent microphone',
        'invalid_ir': 'Invalid IR',
    }
    codes = [issue['code'] for issue in row.get('issues', [])]
    if not codes and row.get('primary_issue'):
        codes = [row['primary_issue']]
    if codes:
        return '; '.join(dict.fromkeys(labels.get(code, 'Measurement issue') for code in codes))
    return {'pass': '', 'excluded': 'Excluded', 'technical': 'Invalid IR',
            'insufficient_neighbours': 'Insufficient neighbours',
            'incomplete': 'Analysis incomplete'}.get(row['status'], 'Measurement issue')


class MeasurementReviewPane(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.rows = []
        self.folder = None
        self.identity = None
        self.busy = False
        self.stale = False
        self.saved_results = False
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.pool_events = queue.Queue()
        self._pool = None
        self._pool_ready = False
        self.generation = 0
        self.controls = []
        layout = QVBoxLayout(self)
        title = QLabel('Measurement Review')
        title.setStyleSheet('font-size: 20px; font-weight: 700;')
        layout.addWidget(title)
        self.path_label = QLabel()
        layout.addWidget(self.path_label)
        tools = QHBoxLayout()
        layout.addLayout(tools)
        self._button(tools, 'Scan', self.scan)
        self.filter = QComboBox()
        self.filter.addItems(['Needs review', 'All recordings', 'Excluded'])
        self.filter.currentTextChanged.connect(self.populate)
        tools.addWidget(self.filter)
        self.recapture_button = self._button(tools, 'Re-capture (0)', lambda: None)
        self.recapture_button.setToolTip('Re-capture is not available yet.')
        self.exclude_button = self._button(tools, 'Exclude (0)', self.exclude)
        self.restore_button = self._button(tools, 'Restore selected', self.restore)
        self._button(tools, 'Delete analysis data', self.cleanup)
        self._button(tools, 'How checks work', self.help)
        tools.addStretch(1)
        self.saved_notice = QLabel()
        self.saved_notice.setWordWrap(True)
        self.saved_notice.setStyleSheet('background: #fff7dd; color: #854d0e; padding: 7px; border: 1px solid #f5d67a;')
        self.saved_notice.hide()
        layout.addWidget(self.saved_notice)
        body = QHBoxLayout()
        layout.addLayout(body, 1)
        self.list_panel = QWidget()
        self.list_panel.setMinimumWidth(260)
        list_layout = QVBoxLayout(self.list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_panel = QWidget()
        self.plot_panel.setMinimumWidth(320)
        plot_layout = QVBoxLayout(self.plot_panel)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.list_panel)
        body.addWidget(self.plot_panel, 1)
        self.table = ReviewTable(0, 4)
        self.select_header = SelectHeader(self.table)
        self.table.setHorizontalHeader(self.select_header)
        self.select_header.toggle_all.connect(self._check_all_visible)
        self.table.toggle_rows.connect(self._set_checked_rows)
        self.table.setHorizontalHeaderLabels(['Select', 'Filename', 'Issue description', 'LF band (Hz)'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setResizeContentsPrecision(-1)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.inspect)
        self.table.itemChanged.connect(self._mark_changed)
        list_layout.addWidget(self.table, 1)
        self.details = QLabel('Scan the current measurement set to review microphone and spatial consistency.')
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        list_layout.addWidget(self.details)
        self.figure = Figure(figsize=(7, 10), layout='constrained')
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setProperty('hoverWheelZoom', True)
        self.plot_interaction = ReviewPlotInteraction(self.canvas)
        plot_layout.addWidget(self.canvas, 1)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.status = QLabel('Ready to scan. No recordings are automatically excluded.')
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.refresh_context()

    def start_pool(self):
        if self._pool is not None:
            return self._pool
        pool = self._pool = ReviewProcessPool()
        self._pool_ready = False
        self.timer.start()
        self.status.setText(f'Warming {pool.workers} review workers…')

        def warm():
            try:
                pool.warm()
                self.pool_events.put((pool, None))
            except Exception as error:
                self.pool_events.put((pool, str(error)))

        threading.Thread(target=warm, daemon=True).start()
        return pool

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.start_pool()
        QTimer.singleShot(0, self._size_list_panel)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, 'table'):
            QTimer.singleShot(0, self._size_list_panel)

    def _size_list_panel(self):
        desired = (self.table.horizontalHeader().length() + self.table.verticalHeader().sizeHint().width()
                   + 2*self.table.frameWidth() + self.table.verticalScrollBar().sizeHint().width())
        self.list_panel.setFixedWidth(max(260, desired))

    def closeEvent(self, event):  # noqa: N802
        self.shutdown()
        super().closeEvent(event)

    def _button(self, layout, text, callback):
        button = QPushButton(text)
        button.setStyleSheet('QPushButton:disabled { color: #94a3b8; border-color: #cbd5e1; background: #f8fafc; }')
        button.clicked.connect(callback)
        layout.addWidget(button)
        self.controls.append(button)
        return button

    def refresh_context(self):
        folder = project.get_project_dir() / 'measurement_set'
        if self.folder != folder:
            self.cancel.set()
            self.generation += 1
            self._set_busy(False)
            self.folder = folder
            self.rows = []
            self.identity = None
            self.stale = False
            self.saved_results = False
            self.saved_notice.hide()
            self.populate()
            self.plot_interaction.clear()
            self.figure.clear()
            self.canvas.draw_idle()
            self.status.setText('Session changed. Scan to review this measurement set.')
            try:
                saved = load_review_results(folder)
                if saved is not None:
                    self.rows, self.identity = saved['rows'], saved['identity']
                    self.saved_results = True
                    self.stale = self.identity is None or dataset_identity(folder) != self.identity
                    self.saved_notice.setText('Saved measurement review results — re-scan to refresh.' +
                                              (' Measurement files have changed or detailed results are unavailable.' if self.stale else ''))
                    self.saved_notice.show()
                    self.populate()
                    self.status.setText(f'{len(self.rows)} saved measurement review results loaded.')
                    if self.table.rowCount():
                        self.table.selectRow(0)
            except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
                self.rows = []
                self.identity = None
                self.stale = True
                self.table.blockSignals(False)
                self.populate()
                self.saved_notice.setText('Saved measurement review could not be loaded. Re-scan to refresh.')
                self.saved_notice.show()
                self.status.setText(f'Saved review unavailable: {error}')
        self.path_label.setText(str(folder))
        if self.identity is not None and not self.busy and dataset_identity(folder) != self.identity:
            self.mark_stale()

    def mark_stale(self):
        if self.rows:
            self.stale = True
            if self.saved_results:
                self.saved_notice.setText('Saved measurement review results — measurement files have changed. Re-scan to refresh.')
            self.status.setText('Measurements changed. Results are stale; rescan before excluding or deleting data.')

    def _capturing(self):
        return bool(self.backend.is_measurement_set_running() or (
            getattr(self.backend, '_measurement_thread', None) is not None
            and self.backend._measurement_thread.is_alive()))

    def _set_busy(self, busy):
        self.busy = busy
        for button in self.controls:
            button.setEnabled(not busy)
        self._update_selection_state()
        self.table.setEnabled(not busy)

    def scan(self):
        if self.busy:
            return
        self.refresh_context()
        if self._capturing():
            QMessageBox.information(self, 'Capture in progress', 'Finish or stop acquisition before reviewing the set.')
            return
        folder = self.folder
        pool = self.start_pool()
        self.cancel = threading.Event()
        cancel = self.cancel
        self.generation += 1
        generation = self.generation
        self._set_busy(True)
        self.status.setText('Scanning microphone recordings and 12-neighbour LF responses…')
        self.progress.setValue(0)

        def work():
            try:
                before = dataset_identity(folder)
                rows = scan_directory(folder, lambda *args: self.events.put((generation, 'progress', args)),
                                      cancel.is_set, pool=pool)
                after = dataset_identity(folder)
                if before != after:
                    raise ValueError('Dataset changed during analysis. Rescan the completed set.')
                if cancel.is_set():
                    raise InterruptedError('Scan cancelled.')
                self.events.put((generation, 'done', (rows, after)))
            except Exception as error:
                self.events.put((generation, 'error', str(error)))
            finally:
                self.events.put((generation, 'finished', None))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def poll(self):
        while True:
            try:
                pool, error = self.pool_events.get_nowait()
            except queue.Empty:
                break
            if pool is self._pool:
                self._pool_ready = error is None
                if not self.busy:
                    self.status.setText(f'Review pool failed: {error}' if error else
                                        f'{pool.workers} review workers ready. Scan the measurement set.')
        while True:
            try:
                generation, kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation:
                # A session switch cancels the old scan; never publish its results.
                continue
            if kind == 'progress':
                phase, done, total = payload
                self.status.setText(f'{phase}: {done}/{total}')
                self.progress.setValue(round(100*done/max(total, 1)))
            elif kind == 'done':
                self.rows, self.identity = payload
                self.stale = False
                self.saved_results = False
                self.saved_notice.hide()
                self.populate()
                try:
                    report = write_review_report(self.folder, self.rows, identity=self.identity)
                    self.status.setText(f'{len(self.rows)} recordings analysed. Report: {report.name}')
                except (OSError, ValueError) as error:
                    self.status.setText(f'Analysis complete; report could not be saved: {error}')
            elif kind == 'error':
                self.status.setText(payload)
            elif kind == 'finished':
                self._set_busy(False)
                if self.table.rowCount():
                    self.table.selectRow(0)

    def populate(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        mode = self.filter.currentText()
        for index, row in enumerate(self.rows):
            if mode == 'Needs review' and row['status'] in ('pass', 'excluded'):
                continue
            if mode == 'Excluded' and row['status'] != 'excluded':
                continue
            number = self.table.rowCount()
            self.table.insertRow(number)
            mark = QTableWidgetItem()
            mark.setData(Qt.ItemDataRole.UserRole, index)
            mark.setFlags(mark.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            mark.setCheckState(Qt.CheckState.Checked if row['marked'] else Qt.CheckState.Unchecked)
            self.table.setItem(number, 0, mark)
            band = f"{row['band_low_hz']:.0f}–{row['band_high_hz']:.0f}" if row.get('bands') else ''
            for column, value in enumerate((row['filename'], short_issue_description(row), band), 1):
                item = QTableWidgetItem(value)
                item.setToolTip((row.get('reason') or row.get('coverage') or value) if column == 2 else value)
                self.table.setItem(number, column, item)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        # The custom Select header also includes a checkbox beside its text.
        self.table.setColumnWidth(0, max(self.table.columnWidth(0),
                                        self.table.fontMetrics().horizontalAdvance('Select')+35))
        QTimer.singleShot(0, self._size_list_panel)
        self._update_selection_state()

    def selected(self):
        return [self.rows[self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)]
                for index in self.table.selectionModel().selectedRows()]

    def _mark_changed(self, item):
        if item.column() == 0:
            highlighted = [index.row() for index in self.table.selectionModel().selectedRows()]
            targets = highlighted if item.row() in highlighted else [item.row()]
            self._set_checked_rows(targets, item.checkState() == Qt.CheckState.Checked)

    def _set_checked_rows(self, numbers, checked):
        self.table.blockSignals(True)
        try:
            for number in numbers:
                item = self.table.item(number, 0)
                if item is not None:
                    self.rows[item.data(Qt.ItemDataRole.UserRole)]['marked'] = checked
                    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        finally:
            self.table.blockSignals(False)
        self._update_selection_state()

    def _check_all_visible(self, checked):
        self._set_checked_rows(range(self.table.rowCount()), checked)

    def _update_selection_state(self):
        checked = [row for row in self.rows if row['marked']]
        active = [row for row in checked if row['status'] != 'excluded']
        self.exclude_button.setText(f'Exclude ({len(active)})')
        self.exclude_button.setEnabled(bool(active) and not self.busy)
        self.recapture_button.setText(f'Re-capture ({len(checked)})')
        self.recapture_button.setEnabled(False)
        self.restore_button.setEnabled(any(row['status'] == 'excluded' for row in checked) and not self.busy)
        visible = [self.table.item(number, 0).checkState() == Qt.CheckState.Checked
                   for number in range(self.table.rowCount())]
        self.select_header.has_rows = bool(visible)
        self.select_header.check_state = (Qt.CheckState.Checked if visible and all(visible) else
                                          Qt.CheckState.PartiallyChecked if any(visible) else Qt.CheckState.Unchecked)
        self.select_header.viewport().update()

    def inspect(self):
        selected = self.selected()
        if not selected or self.busy:
            return
        row = selected[0]
        details = f"{row['filename']} — {row.get('reason', '')}  Coverage: {row.get('coverage', 'Excluded')}."
        for key, label in (('pre', 'Pre-sweep'), ('tail', 'Late')):
            metrics = row.get('metrics', {})
            if f'mic_{key}_dbfs' in metrics:
                details += f"  {label}: {metrics[f'mic_{key}_dbfs']:.1f} dBFS"
                if f'mic_{key}_threshold_dbfs' in metrics:
                    details += f" (threshold {metrics[f'mic_{key}_threshold_dbfs']:.1f} dBFS)"
                if f'{key}_start_s' in metrics and f'{key}_end_s' in metrics:
                    details += f", {metrics[f'{key}_start_s']:.3f}–{metrics[f'{key}_end_s']:.3f} s."
        self.details.setText(details)
        try:
            self.plot_interaction.clear()
            axes = draw_inspection(self.figure, self.folder, row, self.rows)
            self.plot_interaction.set_axes(axes)
            self.canvas.draw_idle()
        except (OSError, ValueError, KeyError) as error:
            self.details.setText(details + f' Plot unavailable: {error}')

    def _can_change(self):
        self.refresh_context()
        if self.busy or self._capturing():
            QMessageBox.information(self, 'Acquisition or analysis in progress', 'Finish acquisition and analysis before changing files.')
            return False
        if self.identity is None or self.stale:
            QMessageBox.information(self, 'Rescan required', 'Scan the current measurement set before changing files.')
            return False
        return True

    def exclude(self):
        self._rename([row for row in self.rows if row['marked'] and row['status'] != 'excluded'])

    def restore(self):
        self._rename([row for row in self.rows if row['marked'] and row['status'] == 'excluded'], True)

    def _rename(self, rows, restore=False):
        if not rows or not self._can_change():
            return
        action = 'Restore' if restore else 'Exclude'
        if QMessageBox.question(self, f'{action} {len(rows)} recordings?',
                                ('Remove' if restore else 'Append') + ' .blacklisted for these recordings?\n\n' +
                                '\n'.join(row['filename'] for row in rows),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        try:
            rename_reviewed(self.folder, rows, restore)
            try:
                write_review_report(self.folder, self.rows, {
                    row['filename']: 'Restored' if restore else 'Excluded' for row in rows})
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, 'Report update failed', f'Files were renamed, but the report could not be updated: {error}')
            self.scan()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Rename failed', str(error))

    def cleanup(self):
        if not self._can_change():
            return
        try:
            plan = analysis_deletion_plan(self.folder)
            space = sum(entry[1] for entry in plan)/1_000_000
            if not plan:
                QMessageBox.information(self, 'No analysis data', 'No retained analysis data to delete.')
                return
            if QMessageBox.question(self, 'Delete analysis data?',
                    f'This will free {space:,.1f} MB by deleting raw captures, full IRs, references and capture diagnostics.\n\n'
                    'Future capture-noise checks and capture reprocessing will be unavailable. '
                    'Linear IRs (including exclusions), spatial review and a saved review report will be kept.\n\n'
                    'Only continue when you are satisfied with the measurements. This cannot be undone.',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
                return
            if not self._can_change():
                return
            write_review_report(self.folder, self.rows)
            delete_analysis_data(self.folder, plan)
            self.scan()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Cleanup stopped', str(error))

    def help(self):
        QMessageBox.information(self, 'Measurement review checks',
            'Scroll over a plot to zoom around the pointer. Hover an axis to zoom only that axis. '
            'Drag with the middle mouse button to pan. Reset axes at the top right restores that plot.\n\n'
            'Use Select checkboxes for actions. Shift-click filenames to highlight a range, then '
            'toggle one checkbox to select or deselect that range. The header checkbox applies '
            'to the displayed list. A text report is saved automatically after analysis and '
            'updated after exclusions/restorations.\n\n'
            'Spatial: 40–320 Hz complex response predicted from 12 neighbours (13 unique valid positions required). '
            'A finding means the prediction differs beyond the detector threshold; it does not establish the cause.\n\n'
            'Pre-sweep: skip max(20 ms, 20% of pre-marker silence); stop 20 ms before the marker. '
            'Post-sweep: use the latter half of final silence, ending 20 ms before recording ends. '
            'Both require 50 ms usable audio. Shading shows the measured regions.\n\n'
            'Post Silence is recorded once. Driver/interface latency uses part of it; increase '
            'Post Silence in Audio Setup if the sweep is truncated or too little decay/noise '
            'analysis time remains.\n\n'
            'Noise must exceed both session and local baselines by max(12 dB, six robust standard deviations), '
            'and exceed −45 dB relative to microphone sweep RMS. At least nine matching captures are required. '
            'Late energy can include room decay. Microphone clipping and silent/constant channels are checked directly.\n\n'
            'No electrical residual or full-IR energy checks run here. Missing checks are shown as incomplete. '
            'Exclusions preserve sample data; rebuild downstream HALS imports after excluding recordings.')

    def shutdown(self):
        self.cancel.set()
        self.generation += 1
        self.timer.stop()
        if self._pool is not None:
            self._pool.close()
            self._pool = None
        self._pool_ready = False
        self._set_busy(False)
