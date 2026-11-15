"""Capture history, approval transactions and targeted scanner integration."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from scipy.io import wavfile

from nfs import review_attempts as review
from nfs.measurement_review import scan_directory
from nfs.review_storage import (dataset_identity, write_review_report, load_review_results,
                                analysis_deletion_plan, delete_analysis_data)
from nfs.datatypes import CylindricalPosition
from nfs.nfs import NearFieldScanner

NAME = 'r100p0_ph0p0_z0p0_ir.wav'


def write_ir(folder, name=NAME, gain=1.):
    folder.mkdir(parents=True, exist_ok=True)
    data = np.zeros(2000, np.float32)
    data[20] = gain
    wavfile.write(folder/name, 4000, data)
    return folder/name


def initial(folder):
    write_ir(folder)
    rows = scan_directory(folder)
    write_review_report(folder, rows, identity=dataset_identity(folder))
    return rows


def successful_attempt(folder, rows, gain=.5):
    job = review.reserve_attempts(folder, rows)[0]
    write_ir(folder/job['folder'], gain=gain)
    review.complete_attempt(folder, job, {})
    return job


def choose(folder, number, excluded=False):
    state = review.load_state(folder)
    state['points'][NAME].update(chosen=number, viewed=number, excluded=excluded)
    review.save_state(folder, state)


def test_attempts_are_immutable_and_approval_is_explicit(tmp_path):
    rows = initial(tmp_path)
    original = (tmp_path/NAME).read_bytes()
    second = successful_attempt(tmp_path, rows)
    third = successful_attempt(tmp_path, rows, gain=.25)
    point = review.load_state(tmp_path)['points'][NAME]
    assert [a['number'] for a in point['attempts']] == [1, 2, 3]
    assert (point['active'], point['chosen'], point['viewed']) == (1, 1, 3)
    assert (tmp_path/NAME).read_bytes() == original
    choose(tmp_path, 2)
    review.accept_capture_list(tmp_path)
    assert (tmp_path/NAME).read_bytes() == (tmp_path/second['folder']/NAME).read_bytes()
    point = review.load_state(tmp_path)['points'][NAME]
    assert point['active'] == point['chosen'] == 2
    assert not point['needs_decision']
    assert (tmp_path/point['attempts'][0]['folder']/NAME).read_bytes() == original
    assert (tmp_path/third['folder']/NAME).is_file()
    # Existing downstream imports still see exactly one IR.
    assert len(list(tmp_path.glob('*.wav'))) == 1
    write_review_report(tmp_path, rows, identity=dataset_identity(tmp_path))
    assert len(review.load_state(tmp_path)['points'][NAME]['attempts']) == 3
    assert load_review_results(tmp_path)['rows']


def test_exclude_and_restore_are_approved_without_losing_alternatives(tmp_path):
    rows = initial(tmp_path)
    successful_attempt(tmp_path, rows)
    choose(tmp_path, 2, excluded=True)
    assert (tmp_path/NAME).exists()
    review.accept_capture_list(tmp_path)
    assert not (tmp_path/NAME).exists()
    assert (tmp_path/(NAME+'.blacklisted')).exists()
    choose(tmp_path, 2)
    review.accept_capture_list(tmp_path)
    assert (tmp_path/NAME).exists()
    assert not (tmp_path/(NAME+'.blacklisted')).exists()
    assert review.load_state(tmp_path)['points'][NAME]['active'] == 2


def test_stopped_and_failed_attempts_keep_original_active(tmp_path):
    rows = initial(tmp_path)
    job = review.reserve_attempts(tmp_path, rows)[0]
    review.complete_attempt(tmp_path, job, error='Stopped')
    point = review.load_state(tmp_path)['points'][NAME]
    assert point['active'] == point['chosen'] == 1
    assert not point['attempts'][-1]['complete']
    choose(tmp_path, 2)
    with pytest.raises(ValueError, match='no complete IR'):
        review.accept_capture_list(tmp_path)
    assert review.load_state(tmp_path)['points'][NAME]['active'] == 1
    assert (tmp_path/NAME).is_file()


def test_approval_failure_rolls_back_every_capture_artifact(tmp_path, monkeypatch):
    rows = initial(tmp_path)
    full = tmp_path/'Distortion'/(NAME[:-4]+'_dist.wav')
    full.parent.mkdir()
    full.write_bytes(b'old-full')
    job = successful_attempt(tmp_path, rows)
    (tmp_path/job['folder']/'Distortion').mkdir()
    (tmp_path/job['folder']/'Distortion'/full.name).write_bytes(b'new-full')
    original = (tmp_path/NAME).read_bytes()
    choose(tmp_path, 2)
    publish = review._publish_file
    calls = []
    def fail_once(folder, relative, source):
        calls.append(relative)
        if relative == NAME and calls.count(NAME) == 1:
            raise OSError('Simulated interruption')
        return publish(folder, relative, source)
    monkeypatch.setattr(review, '_publish_file', fail_once)
    with pytest.raises(OSError, match='interruption'):
        review.accept_capture_list(tmp_path)
    assert full.read_bytes() == b'old-full'
    assert (tmp_path/NAME).read_bytes() == original
    assert review.load_state(tmp_path)['points'][NAME]['active'] == 1
    assert 'publication' not in review.load_state(tmp_path)


def test_recovery_after_process_interruption(tmp_path):
    rows = initial(tmp_path)
    job = successful_attempt(tmp_path, rows)
    state = review.load_state(tmp_path)
    original = state['points'][NAME]['attempts'][0]['folder']
    state['publication'] = dict(operations=[dict(target=NAME, source=str(Path(job['folder'])/NAME),
                                   rollback=str(Path(original)/NAME))], updates={})
    review.save_state(tmp_path, state)
    review._publish_file(tmp_path, NAME, str(Path(job['folder'])/NAME))
    review.recover_publication(tmp_path)
    assert (tmp_path/NAME).read_bytes() == (tmp_path/original/NAME).read_bytes()
    assert 'publication' not in review.load_state(tmp_path)


def test_approval_keeps_shared_references_for_other_points(tmp_path):
    rows = initial(tmp_path)
    ref = tmp_path/'debug'/'references'/'original'/'ref.npz'
    ref.parent.mkdir(parents=True)
    ref.write_bytes(b'original-shared-reference')
    # Legacy bundles without a sidecar retain the only reference.
    job = successful_attempt(tmp_path, rows)
    new_ref = tmp_path/job['folder']/'debug'/'references'/'new'/'ref.npz'
    new_ref.parent.mkdir(parents=True)
    new_ref.write_bytes(b'new-shared-reference')
    choose(tmp_path, 2)
    review.accept_capture_list(tmp_path)
    assert ref.read_bytes() == b'original-shared-reference'
    assert (tmp_path/'debug'/'references'/'new'/'ref.npz').read_bytes() == b'new-shared-reference'


def test_missing_capture_can_be_recovered_or_excluded(tmp_path):
    review.register_missing(tmp_path, NAME, [100., 0., 0.], 'Capture rejected')
    rows = review.scan_review_directory(tmp_path)
    assert len(rows) == 1 and rows[0]['status'] == 'incomplete'
    with pytest.raises(ValueError, match='Choose a capture'):
        review.accept_capture_list(tmp_path)
    job = successful_attempt(tmp_path, rows)
    assert job['number'] == 1
    choose(tmp_path, 1)
    review.accept_capture_list(tmp_path)
    assert (tmp_path/NAME).is_file()


def test_analysis_compares_attempts_to_same_other_positions(tmp_path):
    rows = initial(tmp_path)
    for index in range(1, 14):
        write_ir(tmp_path, f'r100p0_ph{index*20}p0_z0p0_ir.wav')
    successful_attempt(tmp_path, rows)
    successful_attempt(tmp_path, rows, gain=.25)
    result = review.scan_review_directory(tmp_path)
    point = review.load_state(tmp_path)['points'][NAME]
    neighbours = [a['analysis']['neighbours'] for a in point['attempts']]
    assert neighbours[0] == neighbours[1] == neighbours[2]
    assert len(neighbours[0]) == 12 and NAME not in neighbours[0]
    assert len(result) == 14
    assert all(a['analysis']['status'] != 'pass' for a in point['attempts'])  # No raw data is incomplete.


def test_cleanup_keeps_every_linear_attempt_and_decisions(tmp_path):
    rows = initial(tmp_path)
    job = successful_attempt(tmp_path, rows)
    raw = tmp_path/job['folder']/'debug'/'raw.wav'
    raw.parent.mkdir()
    raw.write_bytes(b'noise')
    delete_analysis_data(tmp_path, analysis_deletion_plan(tmp_path))
    assert not raw.exists()
    point = review.load_state(tmp_path)['points'][NAME]
    assert all((tmp_path/a['folder']/NAME).exists() for a in point['attempts'])
    assert (tmp_path/NAME).exists()


def test_scanner_reuses_capture_path_without_touching_grid_or_history(tmp_path):
    folder = tmp_path/'measurement_set'
    rows = initial(folder)
    jobs = review.reserve_attempts(folder, rows)
    log = tmp_path/'positions.csv'
    scanner, audio, original_manager, manager = Mock(), Mock(), Mock(), Mock()
    audio.cap = {}
    scanner.get_position.return_value = CylindricalPosition(100., 0., 0.)
    manager.total_points.return_value = 1
    manager.ready.side_effect = [False, True]
    nfs = NearFieldScanner(scanner, audio, original_manager, str(log))
    nfs._project_dir = tmp_path
    log.write_text('existing history\n')
    order = []
    audio.begin_measurement_set.side_effect = lambda *args: order.append('prepare')
    manager.move_to_safe_starting_radius.side_effect = lambda: order.append('move')
    def capture(ir, full, **kwargs):
        order.append('capture')
        write_ir(ir.parent)
        return {'retries': 0}
    audio.capture_ir.side_effect = capture
    events = []
    nfs.take_measurement_set(progress_callback=events.append, recapture_jobs=jobs, motion_manager=manager)
    assert order == ['prepare', 'move', 'capture']
    assert log.read_text() == 'existing history\n'
    assert not original_manager.mock_calls
    assert audio.begin_measurement_set.call_count == audio.end_measurement_set.call_count == 1
    assert review.load_state(folder)['points'][NAME]['attempts'][-1]['complete']
    assert review.load_state(folder)['points'][NAME]['active'] == 1
    assert audio.set_capture_output_directory.call_args.args[0] == folder
    assert events[-1]['status'] == 'finished'
    assert not nfs.is_measurement_set_running()


def test_wrong_reached_position_never_records(tmp_path):
    folder = tmp_path/'measurement_set'
    jobs = review.reserve_attempts(folder, initial(folder))
    scanner, audio, manager = Mock(), Mock(), Mock()
    scanner.get_position.return_value = CylindricalPosition(500., 0., 0.)
    manager.total_points.return_value = 1
    manager.ready.return_value = False
    nfs = NearFieldScanner(scanner, audio, Mock(), str(tmp_path/'positions.csv'))
    nfs._project_dir = tmp_path
    with pytest.raises(ValueError, match='requested re-capture coordinate'):
        nfs.take_measurement_set(recapture_jobs=jobs, motion_manager=manager)
    audio.capture_ir.assert_not_called()
    assert not nfs.is_measurement_set_running()


def test_no_motion_when_recapture_preparation_stopped(tmp_path):
    from nfs.capture_detection import DetectorPreparationCancelled
    folder = tmp_path/'measurement_set'
    jobs = review.reserve_attempts(folder, initial(folder))
    scanner, audio, manager = Mock(), Mock(), Mock()
    manager.total_points.return_value = 1
    audio.begin_measurement_set.side_effect = DetectorPreparationCancelled()
    nfs = NearFieldScanner(scanner, audio, Mock(), str(tmp_path/'positions.csv'))
    nfs._project_dir = tmp_path
    nfs.take_measurement_set(recapture_jobs=jobs, motion_manager=manager)
    manager.next.assert_not_called()
    manager.move_to_safe_starting_radius.assert_not_called()
    manager.move_to_park_position.assert_not_called()
    audio.capture_ir.assert_not_called()
    assert not nfs.is_measurement_set_running()


def test_settings_validation_allows_silence_changes_but_rejects_gain(tmp_path):
    initial(tmp_path)
    sidecar = tmp_path/'debug'/'r100p0_ph0p0_z0p0_capture.json'
    sidecar.parent.mkdir()
    sidecar.write_text(json.dumps(dict(capture_config=dict(sweep_level_dbfs=-20, pre_sil_ms=200))))
    audio = SimpleNamespace(cap=dict(sweep_level_dbfs=-20, pre_sil_ms=400))
    review.validate_recapture_settings(tmp_path, [dict(filename=NAME)], audio)
    audio.cap['sweep_level_dbfs'] = -10
    with pytest.raises(ValueError, match='sweep_level_dbfs'):
        review.validate_recapture_settings(tmp_path, [dict(filename=NAME)], audio)


def test_gui_viewing_is_independent_from_choice_and_acceptance(qtbot, tmp_path, monkeypatch):
    from harmonic_drive_qt import project, measurement_review as module
    from harmonic_drive_qt.qt_compat import QMessageBox, Qt
    class Pool:
        workers = 1
        def warm(self): pass
        def close(self): pass
        def map(self, function, tasks, cancelled=lambda: False):
            return map(function, tasks)
    folder = tmp_path/'measurement_set'
    rows = initial(folder)
    successful_attempt(folder, rows)
    successful_attempt(folder, rows, gain=.25)
    rows = review.scan_review_directory(folder)
    write_review_report(folder, rows, identity=dataset_identity(folder))
    monkeypatch.setattr(project, 'get_project_dir', lambda: tmp_path)
    monkeypatch.setattr(module, 'ReviewProcessPool', Pool)
    monkeypatch.setattr(module, 'draw_inspection', lambda figure, *args: tuple(figure.subplots(3, 1)))
    pane = module.MeasurementReviewPane(SimpleNamespace(is_measurement_set_running=lambda: False))
    qtbot.addWidget(pane)
    pane.resize(1650, 1000)
    pane.show()
    pane.table.selectRow(0)
    assert len(pane.attempt_group.buttons()) == 3
    assert pane.table.item(0, 4).text() == 'Capture 1'
    from harmonic_drive_qt.review_selection import CAPTURE_COUNT_ROLE
    assert pane.table.item(0, 4).data(CAPTURE_COUNT_ROLE) == 3
    pane.view_capture(NAME, 2)
    assert review.load_state(folder)['points'][NAME]['chosen'] == 1
    assert review.load_state(folder)['points'][NAME]['viewed'] == 2
    pane.choose_capture()
    assert pane.table.item(0, 4).text() == 'Capture 2'
    assert review.load_state(folder)['points'][NAME]['active'] == 1
    # Red/incomplete approval must warn and cancellation must not publish.
    warnings = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: warnings.append(args) or QMessageBox.StandardButton.Cancel)
    pane.accept_list()
    assert warnings and 'did not pass' in warnings[0][2]
    assert review.load_state(folder)['points'][NAME]['active'] == 1
    # Proposed exclusion greys the row, and also remains unapplied until approval.
    pane.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    pane.exclude()
    assert pane.table.item(0, 4).text() == 'Excluded'
    assert pane.table.item(0, 1).foreground().color().name() == '#94a3b8'
    assert (folder/NAME).exists()
    pane.shutdown()
    loaded = module.MeasurementReviewPane(SimpleNamespace(is_measurement_set_running=lambda: False))
    qtbot.addWidget(loaded)
    assert loaded.capture_state['points'][NAME]['chosen'] == 2
    assert loaded.capture_state['points'][NAME]['viewed'] == 2
    assert loaded.table.item(0, 4).text() == 'Excluded'
    loaded.shutdown()


@pytest.mark.parametrize('terminal', ['finished', 'stopped', 'failed'])
def test_full_set_auto_review_waits_until_acquisition_thread_finishes(qtbot, tmp_path, monkeypatch, terminal):
    from harmonic_drive_qt.main_window import MainWindow
    from harmonic_drive_qt import project
    busy = [True]
    calls = []
    monkeypatch.setattr(project, 'get_project_dir', lambda: tmp_path)
    shell = SimpleNamespace(backend=SimpleNamespace(is_measurement_set_running=lambda: busy[0],
        _measurement_thread=SimpleNamespace(is_alive=lambda: busy[0])),
        show_review=lambda: calls.append('open'), review_pane=SimpleNamespace(scan=lambda: calls.append('scan')))
    event = dict(status='failed' if terminal == 'failed' else 'finished', total=3, current=3,
                 stopped_early=terminal == 'stopped')
    MainWindow._review_completed_set(shell, event)
    qtbot.wait(20)
    assert not calls
    busy[0] = False
    if terminal == 'finished':
        qtbot.waitUntil(lambda: calls == ['open', 'scan'])
    else:
        qtbot.wait(120)
        assert not calls


def test_backend_recapture_uses_audio_owner_and_preserves_existing_nfs(tmp_path, monkeypatch):
    from harmonic_drive_qt.backend import BackendManager
    from nfs.motion_manager import MotionManagerFactory
    folder = tmp_path/'measurement_set'
    rows = initial(folder)
    config = tmp_path/'test.ini'
    config.write_text('[nfs]\nmotion_manager = motion\n')
    backend = BackendManager(str(config))
    owner = []
    nfs = SimpleNamespace(_project_dir=tmp_path, _scanner=object(), _audio=SimpleNamespace(),
                          is_measurement_set_running=lambda: False)
    def capture(**kwargs):
        owner.append(threading.current_thread())
        assert kwargs['recapture_jobs'][0]['filename'] == NAME
    nfs.take_measurement_set = capture
    backend.nfs = nfs
    backend.stop_sine = Mock()
    backend.reload_nfs = Mock(side_effect=AssertionError('Must preserve current grid and log'))
    manager = object()
    made = []
    def factory(*args, **kwargs):
        made.append(kwargs['points'])
        return manager
    monkeypatch.setattr(MotionManagerFactory, 'create', factory)
    events = []
    try:
        backend.start_recapture(folder, rows, events.append)
        backend._measurement_thread.join(3)
        assert not backend._measurement_thread.is_alive()
        assert owner == [backend._audio_thread]
        assert backend.nfs is nfs
        assert made[0].total_points() == 1
        assert made[0].next().r() == 100.
        assert events[-1]['status'] == 'recapture_done'
    finally:
        backend.nfs = None
        backend.shutdown()


def test_failed_restoration_of_different_attempt_leaves_only_blacklist(tmp_path, monkeypatch):
    rows = initial(tmp_path)
    job = successful_attempt(tmp_path, rows)
    choose(tmp_path, 1, excluded=True)
    review.accept_capture_list(tmp_path)
    original = (tmp_path/(NAME+'.blacklisted')).read_bytes()
    choose(tmp_path, 2)
    publish = review._publish_file
    failed = []
    def interrupt(folder, relative, source):
        if relative == NAME+'.blacklisted' and source is None and not failed:
            failed.append(True)
            raise OSError('Interrupted restoration')
        return publish(folder, relative, source)
    monkeypatch.setattr(review, '_publish_file', interrupt)
    with pytest.raises(OSError, match='Interrupted restoration'):
        review.accept_capture_list(tmp_path)
    assert not (tmp_path/NAME).exists()
    assert (tmp_path/(NAME+'.blacklisted')).read_bytes() == original
    assert review.load_state(tmp_path)['points'][NAME]['active'] == 1


def test_capture_output_failure_releases_scanner_running_flag(tmp_path):
    folder = tmp_path/'measurement_set'
    jobs = review.reserve_attempts(folder, initial(folder))
    audio, manager = Mock(), Mock()
    audio.set_session_directory.side_effect = OSError('Disk unavailable')
    nfs = NearFieldScanner(Mock(), audio, Mock(), str(tmp_path/'positions.csv'))
    nfs._project_dir = tmp_path
    with pytest.raises(OSError, match='Disk unavailable'):
        nfs.take_measurement_set(recapture_jobs=jobs, motion_manager=manager)
    assert not nfs.is_measurement_set_running()
    manager.move_to_safe_starting_radius.assert_not_called()


def test_history_cannot_reference_an_external_attempt(tmp_path):
    rows = initial(tmp_path)
    successful_attempt(tmp_path, rows)
    state = review.load_state(tmp_path)
    state['points'][NAME]['attempts'][0]['folder'] = '../external'
    review.save_state(tmp_path, state)
    with pytest.raises(ValueError, match='inside the measurement directory'):
        review.load_state(tmp_path)


def test_recapture_handoff_returns_to_new_attempt_for_selected_files(qtbot, tmp_path, monkeypatch):
    from harmonic_drive_qt import project, measurement_review as module
    from harmonic_drive_qt.main_window import MainWindow
    from harmonic_drive_qt.qt_compat import Qt
    class Pool:
        workers = 1
        def warm(self): pass
        def close(self): pass
        def map(self, function, tasks, cancelled=lambda: False): return map(function, tasks)
    folder = tmp_path/'measurement_set'
    initial(folder)
    for angle in (15, 30):
        write_ir(folder, f'r100p0_ph{angle}p0_z0p0_ir.wav')
    rows = review.scan_review_directory(folder)
    write_review_report(folder, rows, identity=dataset_identity(folder))
    originals = {p.name: p.read_bytes() for p in folder.glob('*.wav')}
    monkeypatch.setattr(project, 'get_project_dir', lambda: tmp_path)
    monkeypatch.setattr(module, 'ReviewProcessPool', Pool)
    monkeypatch.setattr(module, 'draw_inspection', lambda figure, *args: tuple(figure.subplots(3, 1)))
    class Backend:
        _measurement_thread = None
        def is_measurement_set_running(self): return False
        def start_recapture(self, folder, selected, callback, **kwargs):
            jobs = review.reserve_attempts(folder, selected)
            def work():
                callback(dict(status='started', current=0, total=len(jobs), recapture=True))
                for index, job in enumerate(jobs):
                    write_ir(folder/job['folder'], job['filename'], gain=.5)
                    review.complete_attempt(folder, job, {})
                    callback(dict(status='point_complete', current=index+1, total=len(jobs), recapture=True))
                callback(dict(status='finished', current=len(jobs), total=len(jobs), recapture=True))
                callback(dict(status='recapture_done', current=len(jobs), total=len(jobs)))
            self._measurement_thread = threading.Thread(target=work)
            self._measurement_thread.start()
    pane = module.MeasurementReviewPane(Backend())
    qtbot.addWidget(pane)
    calls, queued = [], []
    shell = SimpleNamespace(control_pane=SimpleNamespace(set_recapture_points=lambda points: queued.extend(points)),
                            show_machine=lambda: calls.append('machine'), show_live_capture=lambda: calls.append('live'))
    pane.recapture_started.connect(lambda selected: MainWindow._show_recapture(shell, selected))
    pane.recapture_finished.connect(lambda: calls.append('review'))
    pane.show()
    selected_names = [row['filename'] for row in pane.rows[1:]]
    for number in (1, 2):
        pane.table.item(number, 0).setCheckState(Qt.CheckState.Checked)
    pane.recapture()
    assert calls[:2] == ['machine', 'live']
    qtbot.waitUntil(lambda: calls == ['machine', 'live', 'review'] and not pane.busy, timeout=10000)
    assert queued == [dict(r=100., phi=15., z=0.), dict(r=100., phi=30., z=0.)]
    assert set(pane.capture_state['points']) == set(selected_names)
    assert all(len(point['attempts']) == 2 and point['viewed'] == 2 for point in pane.capture_state['points'].values())
    assert pane.selected()[0]['filename'] == selected_names[0]
    assert len(pane.attempt_group.buttons()) == 2
    assert {p.name: p.read_bytes() for p in folder.glob('*.wav')} == originals
    pane.shutdown()


@pytest.mark.parametrize('azimuth', [30., 390.])
def test_recapture_3d_marker_tracks_coordinate_instead_of_original_index(monkeypatch, azimuth):
    import pandas as pd
    from harmonic_drive_qt import live_capture
    monkeypatch.setattr(live_capture, '_get_viewer_settings', lambda: None)
    engine = SimpleNamespace(N=3, curr_idx=0, exact_idx=0, update_plot=Mock())
    pane = SimpleNamespace(progress_engine=engine, progress_canvas=None,
        grid_df=pd.DataFrame(dict(r_xy_mm=[400., 410., 420.], phi_deg=[0., 15., 30.], z_mm=[100., 110., 120.])),
        _active_recapture_position=dict(r=420., phi=azimuth, z=120.))
    live_capture.LiveCapturePane.refresh_progress_viewer(pane, measured_count=1)
    assert engine.curr_idx == engine.exact_idx == 2
    engine.update_plot.assert_called_once()


def test_live_capture_loads_new_attempt_waveforms_without_publishing(tmp_path, monkeypatch):
    import os
    from harmonic_drive_qt import live_capture
    monkeypatch.setattr(live_capture, '_project_dir', lambda: tmp_path)
    folder = tmp_path/'measurement_set'
    canonical = write_ir(folder)
    original = canonical.read_bytes()
    old_raw = folder/'debug'/'point_capture.wav'
    old_raw.parent.mkdir()
    old_raw.write_bytes(b'old raw')
    os.utime(canonical, (1, 1))
    os.utime(old_raw, (1, 1))
    attempt = folder/'review_attempts'/'point'/'capture_0002'
    new_ir = write_ir(attempt, gain=.5)
    raw = attempt/'debug'/'point_capture.wav'
    raw.parent.mkdir()
    raw.write_bytes(b'new raw')
    assert live_capture._find_latest_ir_file() == new_ir
    assert live_capture._find_latest_capture_file() == raw
    assert canonical.read_bytes() == original
