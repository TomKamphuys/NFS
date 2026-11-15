"""Full-width native measurement review, with cancellable background analysis."""
import queue
import threading

from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidgetItem, QButtonGroup, QScrollArea
from PySide6.QtGui import QColor
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from nfs.review_attempts import (scan_review_directory as scan_directory, load_state, save_state,
    ensure_point, point_name, attempt_row, attempt_quality, accept_capture_list, recover_publication)
from nfs.review_pool import ReviewProcessPool
from nfs.review_storage import (write_review_report, load_review_results, dataset_identity, analysis_deletion_plan,
                                delete_analysis_data)
from . import project
from .qt_compat import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
                        QProgressBar, QTimer, QMessageBox, Qt, Signal)
from .review_plots import draw_inspection
from .review_interaction import ReviewPlotInteraction
from .review_selection import ReviewTable, CAPTURE_COUNT_ROLE
from .styles import header_combo


def short_issue_description(row):
    labels = {
        'pre_sweep_noise': 'Pre-sweep noise',
        'late_noise': 'Post-sweep noise',
        'spatial_discrepancy': 'LF response outside prediction',
        'clipping': 'Microphone clipping',
        'silent_channel': 'Silent microphone',
        'invalid_ir': 'Invalid IR',
        'missing_capture': 'Missing capture',
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
    recapture_started = Signal(object)
    recapture_finished = Signal()

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.capture_state = {'points': {}}
        self.recapture_pending = False
        self.detector_callback = None
        self.capture_progress = None
        self._return_filename = None
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
        tools = QHBoxLayout()
        layout.addLayout(tools)
        scan_button = self._button(tools, 'Scan', self.scan)
        self.filter = QComboBox()
        header_combo(self.filter)
        scan_button.ensurePolished()
        self.filter.setFixedHeight(scan_button.sizeHint().height())
        self.filter.setStyleSheet(self.filter.styleSheet() +
            'QComboBox { border-radius: 4px; font-size: 10pt; }'
            'QComboBox::drop-down { background: transparent; border: none; margin: 1px; }')
        self.filter.addItems(['Needs review', 'All recordings', 'Excluded'])
        self.filter.currentTextChanged.connect(self.populate)
        tools.addWidget(self.filter)
        self.recapture_button = self._button(tools, 'Re-capture (0)', self.recapture)
        self.pause_button = self._button(tools, 'Pause', self.pause_recapture)
        self.stop_button = self._button(tools, 'Stop', self.backend.stop_measurement_set if hasattr(self.backend, 'stop_measurement_set') else lambda: None)
        self.pause_button.hide()
        self.stop_button.hide()
        self.exclude_button = self._button(tools, 'Exclude (0)', self._exclude_or_restore)
        self.accept_button = self._button(tools, 'Accept capture list', self.accept_list)
        self._button(tools, 'Delete analysis data', self.cleanup)
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
        badge_legend = QHBoxLayout()
        badge_legend.setSpacing(6)
        badge_example = QLabel('2')
        badge_example.setFixedSize(18, 18)
        badge_example.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge_example.setStyleSheet('background: #2563eb; color: white; border-radius: 9px; '
                                   'font-size: 10px; font-weight: 700;')
        badge_example.setAccessibleName('Example badge: two captures available')
        badge_legend.addWidget(badge_example)
        badge_description = QLabel('Number of captures available to choose from.')
        badge_description.setWordWrap(True)
        badge_description.setStyleSheet('font-size: 11px; color: #64748b;')
        badge_legend.addWidget(badge_description, 1)
        list_layout.addLayout(badge_legend)
        self.plot_panel = QWidget()
        self.plot_panel.setMinimumWidth(320)
        plot_layout = QVBoxLayout(self.plot_panel)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.list_panel)
        body.addWidget(self.plot_panel, 1)
        self.table = ReviewTable(0, 5)
        self.select_header = self.table.horizontalHeader()
        self.table.toggle_rows.connect(self._set_checked_rows)
        self.table.setHorizontalHeaderLabels(['Select', 'Filename', 'Issue description', 'LF band (Hz)', 'Chosen capture'])
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
        attempt_row = QHBoxLayout()
        plot_layout.addLayout(attempt_row)
        self.attempt_bar = QHBoxLayout()
        self.attempt_bar.setContentsMargins(0, 0, 0, 0)
        attempt_widget = QWidget()
        attempt_widget.setLayout(self.attempt_bar)
        attempt_scroll = self.attempt_scroll = QScrollArea()
        attempt_scroll.setWidgetResizable(True)
        attempt_scroll.setWidget(attempt_widget)
        attempt_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        attempt_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        attempt_scroll.setFixedHeight(54)
        attempt_row.addWidget(attempt_scroll, 1)
        self.attempt_group = QButtonGroup(self)
        self.attempt_group.setExclusive(True)
        self.choose_button = QPushButton('Choose this capture')
        self.choose_button.clicked.connect(self.choose_capture)
        attempt_row.addWidget(self.choose_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.choose_button.setEnabled(False)
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
            self.capture_state = {'points': {}}
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
                recover_publication(folder)
                self.capture_state = load_state(folder)
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
        self.attempt_scroll.setEnabled(not busy)
        if busy:
            self.choose_button.setEnabled(False)

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
                recover_publication(folder)
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
            if kind == 'capture':
                if self.capture_progress and payload.get('status') != 'recapture_done':
                    self.capture_progress(payload)
                self.status.setText(f"Re-capture: {payload.get('status')} {payload.get('current', 0)}/{payload.get('total', 0)}" +
                                    (f" — {payload['error']}" if payload.get('error') else ''))
                self.progress.setValue(round(100*payload.get('current', 0)/max(1, payload.get('total', 1))))
                if payload.get('status') == 'recapture_done':
                    self.recapture_pending = True
            elif kind == 'progress':
                phase, done, total = payload
                self.status.setText(f'{phase}: {done}/{total}')
                self.progress.setValue(round(100*done/max(total, 1)))
            elif kind == 'done':
                self.rows, self.identity = payload
                self.capture_state = load_state(self.folder)
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
                    target = next((n for n in range(self.table.rowCount())
                                   if self.table.item(n, 1).text() == self._return_filename), 0)
                    self.table.selectRow(target)
                    self.table.scrollToItem(self.table.item(target, 1))
                self._return_filename = None

        if self.recapture_pending and not self._capturing():
            self.recapture_pending = False
            self.pause_button.hide()
            self.stop_button.hide()
            self._set_busy(False)
            self.recapture_finished.emit()
            self.scan()

    def populate(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        mode = self.filter.currentText()
        for index, row in enumerate(self.rows):
            point = self.capture_state['points'].get(point_name(row), {})
            excluded = point.get('excluded', row['status'] == 'excluded')
            chosen_attempt = next((a for a in point.get('attempts', []) if a['number'] == point.get('chosen')), None)
            shown = attempt_row(point_name(row), chosen_attempt) if chosen_attempt and chosen_attempt.get('analysis') else row
            if mode == 'Needs review' and row['status'] in ('pass', 'excluded') and not point.get('needs_decision'):
                continue
            if mode == 'Excluded' and not excluded:
                continue
            number = self.table.rowCount()
            self.table.insertRow(number)
            mark = QTableWidgetItem()
            mark.setData(Qt.ItemDataRole.UserRole, index)
            mark.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            mark.setCheckState(Qt.CheckState.Checked if row['marked'] else Qt.CheckState.Unchecked)
            self.table.setItem(number, 0, mark)
            band = f"{shown['band_low_hz']:.0f}–{shown['band_high_hz']:.0f}" if shown.get('bands') else ''
            chosen = 'Excluded' if excluded else f"Capture {point.get('chosen', 1) or '—'}"
            available = sum(bool(attempt.get('complete')) for attempt in point.get('attempts', []))
            for column, value in enumerate((point_name(row), short_issue_description(shown), band, chosen), 1):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if excluded:
                    item.setForeground(QColor('#94a3b8'))
                item.setToolTip((shown.get('reason') or shown.get('coverage') or value) if column == 2 else value)
                if column == 4 and available > 1:
                    item.setData(CAPTURE_COUNT_ROLE, available)
                    item.setToolTip(f'{available} captures available. Select this row to compare them above the plots.')
                    item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, f'{available} captures available')
                self.table.setItem(number, column, item)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        QTimer.singleShot(0, self._size_list_panel)
        self._update_selection_state()
        if not self.table.rowCount():
            self._show_attempts(dict(filename='No capture'))
            self.plot_interaction.clear()
            self.figure.clear()
            self.canvas.draw_idle()

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
        active = [row for row in checked if not self.capture_state['points'].get(point_name(row), {}).get('excluded', row['status'] == 'excluded')]
        restoring = self.filter.currentText() == 'Excluded'
        count = len(checked)-len(active) if restoring else len(active)
        self.exclude_button.setText(f'{"Restore" if restoring else "Exclude"} ({count})')
        self.exclude_button.setEnabled(bool(count) and not self.busy)
        self.recapture_button.setText(f'Re-capture ({len(checked)})')
        self.recapture_button.setEnabled(bool(checked) and not self.busy)

    def inspect(self):
        selected = self.selected()
        if not selected or self.busy:
            return
        row = selected[0]
        self._show_attempts(row)
        point = self.capture_state['points'].get(point_name(row), {})
        attempt = next((a for a in point.get('attempts', []) if a['number'] == point.get('viewed')), None)
        plot_folder = self.folder
        if attempt:
            row = attempt_row(point_name(row), attempt)
            plot_folder = self.folder/attempt['folder']
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
            axes = draw_inspection(self.figure, plot_folder, row, self.rows)
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

    def _save_choices(self):
        save_state(self.folder, self.capture_state)
        self.identity = dataset_identity(self.folder)
        write_review_report(self.folder, self.rows, identity=self.identity)

    def _show_attempts(self, row):
        while self.attempt_bar.count():
            item = self.attempt_bar.takeAt(0)
            if item.widget():
                self.attempt_group.removeButton(item.widget())
                item.widget().deleteLater()
        name = point_name(row)
        point = self.capture_state['points'].get(name, {})
        attempts = point.get('attempts', [])
        viewed = point.get('viewed')
        if not attempts:
            button = QPushButton('Capture 1 (original)' if 'response' in row else 'No capture')
            button.setEnabled(False)
            self.attempt_bar.addWidget(button)
        for attempt in attempts:
            quality = attempt_quality(attempt)
            color = {'pass': '#15803d', 'fail': '#b91c1c', 'incomplete': '#a16207'}[quality]
            label = f"Capture {attempt['number']}" + (' ✓ chosen' if point.get('chosen') == attempt['number'] and not point.get('excluded') else '')
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(viewed == attempt['number'])
            button.setToolTip({'pass': 'Passed automatic checks', 'fail': 'Automatic checks flagged this capture', 'incomplete': 'Analysis incomplete or capture unavailable'}[quality])
            button.setStyleSheet(f'QPushButton {{color:{color}; padding:6px;}} QPushButton:checked {{border:2px solid #2563eb; background:#dbeafe;}}')
            self.attempt_group.addButton(button)
            button.clicked.connect(lambda checked=False, n=attempt['number'], key=name: self.view_capture(key, n))
            self.attempt_bar.addWidget(button)
        self.attempt_bar.addStretch(1)
        viewed_attempt = next((a for a in attempts if a['number'] == viewed), None)
        self.choose_button.setEnabled(bool(viewed_attempt and viewed_attempt['complete']) and not self.busy)

    def view_capture(self, name, number):
        self.capture_state['points'][name]['viewed'] = number
        save_state(self.folder, self.capture_state)
        self.inspect()

    def choose_capture(self):
        if not self._can_change() or not self.selected():
            return
        name = point_name(self.selected()[0])
        point = self.capture_state['points'][name]
        point.update(chosen=point['viewed'], excluded=False, needs_decision=True)
        self._save_choices()
        self._refresh_choice_row(name)

    def _refresh_choice_row(self, name):
        self.populate()
        for number in range(self.table.rowCount()):
            if self.table.item(number, 1).text() == name:
                self.table.selectRow(number)
                break

    def exclude(self):
        self._stage_exclusions(True)

    def _exclude_or_restore(self):
        if self.filter.currentText() == 'Excluded':
            self.restore()
        else:
            self.exclude()

    def restore(self):
        self._stage_exclusions(False)

    def _stage_exclusions(self, excluded):
        rows = [row for row in self.rows if row['marked'] and
                self.capture_state['points'].get(point_name(row), {}).get('excluded', row['status'] == 'excluded') != excluded]
        if not rows or not self._can_change():
            return
        try:
            for row in rows:
                point = ensure_point(self.folder, self.capture_state, row)
                point.update(excluded=excluded, needs_decision=True)
            self._save_choices()
            self.populate()
            self.status.setText('Capture list updated. Accept capture list to apply these choices.')
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Could not update capture list', str(error))

    def accept_list(self):
        if not self._can_change():
            return
        warnings = []
        for name, point in self.capture_state['points'].items():
            if point['excluded']:
                continue
            attempt = next((a for a in point['attempts'] if a['number'] == point.get('chosen')), None)
            if attempt is None or not attempt['complete']:
                QMessageBox.warning(self, 'Capture required', f'Choose a complete capture or exclude {name}.')
                return
            if attempt_quality(attempt) != 'pass':
                warnings.append(name)
        # Include untouched flagged original captures in the approval warning.
        warnings.extend(point_name(r) for r in self.rows if point_name(r) not in self.capture_state['points']
                        and r['status'] not in ('pass', 'excluded'))
        if warnings and QMessageBox.question(self, 'Accept flagged captures?',
                f'{len(warnings)} accepted captures did not pass all automatic checks. Are you sure you want to use them?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        try:
            accept_capture_list(self.folder)
            self.capture_state = load_state(self.folder)
            actions = {}
            for row in self.rows:
                name = point_name(row)
                point = self.capture_state['points'].get(name, {})
                if point or row['status'] != 'pass':
                    actions[name] = ('Excluded' if point.get('excluded', row['status'] == 'excluded')
                                     else f"Approved capture {point.get('active', 1)}")
            write_review_report(self.folder, self.rows, actions)
            self.scan()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Approval failed', str(error))

    def recapture(self):
        rows = [row for row in self.rows if row['marked']]
        if not rows or not self._can_change():
            return
        try:
            generation = self.generation
            self.backend.start_recapture(self.folder, rows,
                lambda event: self.events.put((generation, 'capture', event)), detector_callback=self.detector_callback)
            self._set_busy(True)
            self.pause_button.setEnabled(True)
            self.stop_button.setEnabled(True)
            self.pause_button.setText('Pause')
            self.pause_button.show()
            self.stop_button.show()
            self.choose_button.setEnabled(False)
            self.status.setText('Re-capturing selected coordinates…')
            self._return_filename = point_name(rows[0])
            self.recapture_started.emit(rows)
        except (OSError, ValueError, RuntimeError) as error:
            self.capture_state = load_state(self.folder)
            QMessageBox.warning(self, 'Re-capture could not start', str(error))

    def pause_recapture(self):
        if self.backend.is_measurement_set_paused():
            self.backend.resume_measurement_set()
            self.pause_button.setText('Pause')
        else:
            self.backend.pause_measurement_set()
            self.pause_button.setText('Resume')

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
            'toggle one checkbox to select or deselect that range. Ctrl+A highlights all displayed rows. '
            'A text report is saved automatically after analysis and '
            'updated after approvals and exclusions. Choose an attempt above the plots, then use '
            'Choose this capture. Accept capture list publishes the chosen attempts and exclusions.\n\n'
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
