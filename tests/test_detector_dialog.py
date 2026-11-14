"""Shared detector GUI and decision tests; no hardware or scanner movement."""

import os
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

from harmonic_drive_qt.detector_dialog import DetectorDialog
from harmonic_drive_qt.qt_compat import QPushButton, Qt
from nfs.capture_detection.events import DetectorDecision, DetectorEvent


def test_decision_keeps_first_choice_and_cancels_worker_wait():
    decision = DetectorDecision()
    assert decision.wait(lambda: False) == 'cancel'
    decision.choose('continue')
    assert decision.choice == 'cancel'


def test_reference_progress_uses_duration_and_completed_count(qtbot):
    dialog = DetectorDialog(Mock())
    qtbot.addWidget(dialog)
    assert dialog.windowModality() == Qt.WindowModality.NonModal
    dialog.present(DetectorEvent('preparation_progress', 'Reference', 'Captured 1 of 3',
                                  completed=1, total=3, duration_s=10))
    dialog._started -= 5
    dialog._tick()
    assert dialog.progress.value() == pytest.approx(500, abs=5)
    assert dialog.isVisible()
    dialog.dismiss()


def test_reference_success_acknowledges_after_display(qtbot):
    dialog = DetectorDialog(Mock())
    qtbot.addWidget(dialog)
    decision = DetectorDecision()
    dialog.present(DetectorEvent('preparation_ready', 'Reference ready', 'The three loopbacks match.',
                                  hold_seconds=.05, decision=decision))
    assert decision.choice is None
    assert dialog.icon.accessibleName() == 'Accepted'
    assert not dialog.icon.pixmap().isNull()
    qtbot.waitUntil(lambda: decision.choice == 'continue')
    assert not dialog.isVisible()


@pytest.mark.parametrize('choice', ['disable_for_scan', 'cancel'])
def test_reference_failure_actions_release_decision(qtbot, choice):
    stop = Mock()
    dialog = DetectorDialog(stop)
    qtbot.addWidget(dialog)
    decision = DetectorDecision()
    dialog.present(DetectorEvent('preparation_failed', 'Reference failed', 'Loopbacks do not match.',
        details='Review Audio Setup.', decision=decision,
        actions=(('disable_for_scan', 'Continue without this detector'), ('cancel', 'Cancel scan'))))
    assert dialog.icon.accessibleName() == 'Rejected'
    assert not dialog.icon.pixmap().isNull()
    label = 'Cancel scan' if choice == 'cancel' else 'Continue without this detector'
    next(button for button in dialog.findChildren(QPushButton) if button.text() == label).click()
    assert decision.choice == choice
    assert stop.call_count == (1 if choice == 'cancel' else 0)
    assert not dialog.isVisible()


def test_closing_dialog_cancels_pending_decision(qtbot):
    stop = Mock()
    dialog = DetectorDialog(stop)
    qtbot.addWidget(dialog)
    decision = DetectorDecision()
    dialog.present(DetectorEvent('preparation_failed', 'Reference failed', 'Mismatch', decision=decision))
    dialog.close()
    assert decision.choice == 'cancel'
    stop.assert_called_once()


def test_old_success_timer_cannot_close_a_new_capture(qtbot):
    dialog = DetectorDialog(Mock())
    qtbot.addWidget(dialog)
    dialog.present(DetectorEvent('point_accepted', 'Accepted', 'Accepted', hold_seconds=.01))
    dialog.present(DetectorEvent('retry_started', 'Repeating', 'Next point', attempt=2, max_attempts=3))
    qtbot.wait(30)
    assert dialog.isVisible()
    assert dialog.title.text() == 'Repeating'
    dialog.dismiss()


@pytest.mark.parametrize('choice', ['disable_for_scan', 'cancel'])
def test_real_reference_failure_waits_for_gui_choice(qtbot, monkeypatch, choice):
    import threading
    from harmonic_drive_qt.qt_compat import QObject, Signal
    from nfs.capture_detection import DetectorPreparationCancelled
    from test_audio_capture import make_engine, ideal_capture, _single_reference_capture

    class Bridge(QObject):
        message = Signal(object)

    engine = make_engine()
    engine.cap['retry_check_loopback_reference'] = True
    plan = engine._prepare_sweep()
    mic, loop = ideal_capture(plan)
    capture = _single_reference_capture(engine)
    bad = dict(capture, raw_recordings=(capture['raw_recordings'][0] * 1.1, capture['raw_recordings'][1]))
    monkeypatch.setattr(engine, '_run_sweep', Mock(side_effect=[capture, bad, capture]))
    dialog = DetectorDialog(Mock())
    qtbot.addWidget(dialog)
    bridge = Bridge()
    bridge.message.connect(dialog.present)
    engine.set_detector_observer(bridge.message.emit)
    errors = []

    def prepare():
        try:
            engine.begin_measurement_set()
        except DetectorPreparationCancelled as error:
            errors.append(error)

    worker = threading.Thread(target=prepare, daemon=True)
    worker.start()
    try:
        qtbot.waitUntil(lambda: dialog._event is not None and dialog._event.kind == 'preparation_failed')
        assert worker.is_alive()  # Scanner worker cannot advance before the user's choice.
        assert not engine._measurement_set_active
        label = 'Cancel scan' if choice == 'cancel' else 'Continue without this detector'
        next(b for b in dialog.findChildren(QPushButton) if b.text() == label).click()
        qtbot.waitUntil(lambda: not worker.is_alive())
        assert bool(errors) == (choice == 'cancel')
        assert engine._measurement_set_active == (choice == 'disable_for_scan')
        assert engine.cap['retry_check_loopback_reference'] is True
    finally:
        dialog.dismiss()
        worker.join(2)
