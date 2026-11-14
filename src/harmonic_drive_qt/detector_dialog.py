"""Shared live-detector presentation; algorithms and scan policy remain in core."""

import time

from .qt_compat import (QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                        QTextEdit, QStyle, QTimer, QVBoxLayout, Qt)


class DetectorDialog(QDialog):
    def __init__(self, stop_scan, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Capture fault detection')
        # Keep scan Pause/Resume and Stop reachable. The worker's decision wait,
        # rather than window modality, prevents motion before a choice is made.
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setMinimumWidth(510)
        self.resize(560, 300)
        self._stop_scan = stop_scan
        self._event = None
        self._generation = 0
        self._started = 0.0
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        self.icon = QLabel()
        self.icon.setFixedSize(48, 48)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        self.title.setStyleSheet('font-size: 18px; font-weight: 700;')
        heading.addWidget(self.icon)
        heading.addWidget(self.title, 1)
        layout.addLayout(heading)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.message)
        self.point = QLabel()
        self.point.setWordWrap(True)
        self.point.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.point)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setStyleSheet(
            'QProgressBar { border: 1px solid #cbd5e1; border-radius: 4px; '
            'background: #f1f5f9; color: #0f172a; text-align: center; min-height: 22px; } '
            'QProgressBar::chunk { background: #93c5fd; border-radius: 3px; }')
        layout.addWidget(self.progress)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(150)
        layout.addWidget(self.details)
        self.buttons = QHBoxLayout()
        layout.addLayout(self.buttons)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)

    def present(self, event):
        self._generation += 1
        self._event = event
        self._started = time.monotonic()
        self.title.setText(event.title)
        self.message.setText(event.message)
        self.point.setText((f'Point: {event.point}\n' if event.point else '')
                           + (f'Attempt {event.attempt} of {event.max_attempts}' if event.attempt else ''))
        self.point.setVisible(bool(event.point or event.attempt))
        self.details.setPlainText(event.details)
        self.details.setVisible(bool(event.details))
        good = event.kind in ('preparation_ready', 'point_accepted')
        bad = event.kind in ('preparation_failed', 'attempt_rejected', 'point_failed')
        self.icon.setAccessibleName('Accepted' if good else 'Rejected' if bad else 'In progress')
        if good or bad:
            symbol = QStyle.StandardPixmap.SP_DialogApplyButton if good else QStyle.StandardPixmap.SP_DialogCancelButton
            self.icon.setPixmap(self.style().standardIcon(symbol).pixmap(36, 36))
        else:
            self.icon.setText('…')
        self.icon.setStyleSheet('font-size: 36px; font-weight: 700; color: '
                               + ('#15803d;' if good else '#b91c1c;' if bad else '#2563eb;'))
        self.progress.setRange(0, 0 if event.kind == 'checking' else 1000)
        self.progress.setFormat('%p%')
        self.progress.setVisible(event.kind not in ('preparation_failed', 'attempt_rejected', 'point_failed'))
        self.progress.setValue(1000 if good else 0)
        while self.buttons.count():
            item = self.buttons.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for action, label in event.actions:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, choice=action: self._choose(choice))
            self.buttons.addWidget(button)
        if not event.actions:
            cancel = QPushButton('Cancel scan')
            cancel.clicked.connect(self.reject)
            self.buttons.addStretch(1)
            self.buttons.addWidget(cancel)
        self.timer.start()
        self._tick()
        self.show()
        self.raise_()
        if event.hold_seconds:
            generation = self._generation
            QTimer.singleShot(round(event.hold_seconds * 1000),
                              lambda: self._complete(generation))

    def _tick(self):
        event = self._event
        if event is None or event.kind not in ('preparing', 'preparation_progress', 'retry_started'):
            return
        total = event.total or 1
        estimated = 0.0
        if event.duration_s > 0 and event.completed < total:
            estimated = min(.95, (time.monotonic() - self._started) / event.duration_s)
        self.progress.setValue(round(1000 * min(1, (event.completed + estimated) / total)))
        self.progress.setFormat(
            f'{event.completed} of {total} sequences complete · estimated progress' if event.total else
            'Replacement capture · estimated progress')

    def _complete(self, generation):
        if generation == self._generation:
            self._choose('continue')

    def _choose(self, choice):
        if self._event is None:
            return
        if self._event is not None and self._event.decision is not None:
            self._event.decision.choose(choice)
        if choice == 'cancel':
            self._stop_scan()
        self._event = None
        self._generation += 1
        self.timer.stop()
        self.hide()

    def dismiss(self):
        if self._event is not None and self._event.decision is not None:
            self._event.decision.choose('cancel')
        self._event = None
        self._generation += 1
        self.timer.stop()
        self.hide()

    def reject(self):
        self._choose('cancel')

    def closeEvent(self, event):
        self._choose('cancel')
        event.accept()
