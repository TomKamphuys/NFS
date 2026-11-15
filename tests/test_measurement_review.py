"""Behavioural coverage for microphone review and file lifecycle."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.io import wavfile

from nfs.measurement_review import (coordinates, inspect_all, scan_directory, rename_reviewed)
from nfs.review_capture import noise_windows, compare_noise, microphone_metrics
from nfs.review_storage import analysis_deletion_plan, delete_analysis_data


@pytest.fixture
def review_pools(monkeypatch):
    """Keep UI tests independent of this machine's logical processor count."""
    from harmonic_drive_qt import measurement_review
    pools = []

    class FakePool:
        workers = 2

        def __init__(self):
            self.closed = False
            pools.append(self)

        def warm(self):
            assert not self.closed

        def map(self, function, tasks, cancelled=lambda: False):
            for task in tasks:
                if cancelled():
                    raise InterruptedError('Scan cancelled.')
                yield function(task)

        def close(self):
            self.closed = True

    monkeypatch.setattr(measurement_review, 'ReviewProcessPool', FakePool)
    return pools


def test_adaptive_windows_and_minimum_duration():
    assert noise_windows(1000, 2000, 500, 1200) == {'pre': (100, 480), 'tail': (1600, 1980)}
    assert noise_windows(1000, 1000, 100, 800) == {'pre': (20, 80), 'tail': (900, 980)}
    assert noise_windows(1000, 1000, 89, 862) == {'pre': None, 'tail': None}
    assert noise_windows(1000, 1000, 90, 860) == {'pre': (20, 70), 'tail': (930, 980)}


def test_both_native_naming_formats_have_same_coordinates():
    np.testing.assert_allclose(coordinates('r100p0_ph90p0_z20p0_ir.wav')[0],
                               coordinates('(100.0, 90.0, 20.0).wav')[0])


def test_single_neighbourhood_clean_field_and_injected_anomaly():
    rng = np.random.default_rng(42)
    xyz = rng.uniform(-1, 1, (50, 3))
    response = np.tile((1 + .02*xyz[:, 0])[:, None], (1, 37)).astype(complex)
    assert all(row['status'] == 'pass' for row in inspect_all(xyz, response))
    response[7] *= 2
    results = inspect_all(xyz, response)
    assert results[7]['status'] == 'repeat'
    assert all(len(band['checks']) == 1 and band['checks'][0]['neighbours'] == 12 for band in results[7]['bands'])
    assert all(row['status'] == 'insufficient_neighbours' for row in inspect_all(xyz[:12], response[:12]))


def test_noise_threshold_requires_local_session_and_relative_departure():
    def make(levels, relative=-30):
        return [dict(xyz=[i, 0, 0], noise_group='same', metrics={'mic_pre_dbfs': value,
                     'mic_pre_relative_db': relative}, issues=[], coverage_notes=[])
                for i, value in enumerate(levels)]
    rows = make([-80]*12 + [-60])
    compare_noise(rows)
    assert rows[-1]['issues'][0]['code'] == 'pre_sweep_noise'
    assert rows[-1]['metrics']['mic_pre_threshold_dbfs'] == -68
    quiet = make([-100]*12 + [-80], -60)
    compare_noise(quiet)
    assert not quiet[-1]['issues']
    # Different capture timings must not provide a baseline for one another.
    rows[-1]['noise_group'] = 'different'
    rows[-1]['issues'] = []
    compare_noise(rows)
    assert not rows[-1]['issues']
    assert 'unavailable' in rows[-1]['coverage_notes'][0]


def write_capture(folder, name='r100p0_ph0p0_z0p0_ir.wav', repeats=1):
    fs = 4000
    rng = np.random.default_rng(8)
    marker = rng.normal(0, .1, 80)
    starts = [800 + i*2200 for i in range(repeats)]
    length = starts[-1]+3000
    raw = rng.normal(0, .0001, (length, 2))
    for start in starts:
        raw[start:start+80, 0] = marker
        raw[start+160:start+1760, 1] = .1*np.sin(np.arange(1600))
    debug = folder/'debug'
    reference = debug/'references'/'session'/'reference.npz'
    reference.parent.mkdir(parents=True)
    layout = dict(schema='marker_then_dual_sweep_v1', playback_marker_starts=starts,
                  sweep_offset_samples=160, sweep_samples=1600, total_samples=length)
    metadata = dict(sample_rate=fs, raw_capture_channels=['electrical_loopback', 'microphone'], layout=layout)
    np.savez(reference, marker=marker, metadata_json=json.dumps(metadata))
    stem = name.removesuffix('_ir.wav')
    wavfile.write(debug/(stem+'_capture.wav'), fs, raw.astype(np.float32))
    row = dict(filename=name, metrics={}, issues=[], coverage_notes=[])
    return row, reference


def test_raw_capture_windows_support_repeats_and_explicit_references(tmp_path):
    row, reference = write_capture(tmp_path, repeats=2)
    # Another archive is legal when a capture has an explicit association.
    np.savez(reference.with_name('other.npz'), ignored=[1])
    stem = row['filename'].removesuffix('_ir.wav')
    (tmp_path/'debug'/(stem+'_capture.json')).write_text(json.dumps({'reference_file': 'references/session/reference.npz'}))
    windows = microphone_metrics(tmp_path, row)
    assert windows['pre'] == (160, 720)
    assert row['metrics']['tail_start_s'] > (3000+1760)/4000
    assert not row['issues']


def test_missing_captures_are_incomplete_and_rename_restore_safe(tmp_path):
    name = 'r100p0_ph0p0_z0p0_ir.wav'
    signal = np.zeros(2000, np.float32)
    signal[50] = 1
    wavfile.write(tmp_path/name, 4000, signal)
    rows = scan_directory(tmp_path)
    assert rows[0]['status'] == 'insufficient_neighbours'
    assert 'unavailable' in rows[0]['coverage']
    rename_reviewed(tmp_path, rows)
    excluded = scan_directory(tmp_path)
    assert excluded[0]['status'] == 'excluded'
    wavfile.write(tmp_path/name, 4000, signal)
    with pytest.raises(FileExistsError):
        rename_reviewed(tmp_path, excluded, restore=True)
    (tmp_path/name).unlink()
    rename_reviewed(tmp_path, excluded, restore=True)
    assert (tmp_path/name).exists()


def test_cleanup_preserves_linear_exclusions_reports_and_rejects_changed_plan(tmp_path):
    for relative in ('r100_ph0_z0_ir.wav', 'r100_ph0_z1_ir.wav.blacklisted', 'review.csv',
                     'debug/a_capture.wav', 'debug/a_capture.json', 'debug/references/s/a.npz',
                     'Distortion/a_ir_dist.wav', 'failed/p/attempt_01_capture.wav'):
        path = tmp_path/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'data')
    plan = analysis_deletion_plan(tmp_path)
    assert len(plan) == 5
    (tmp_path/'debug/a_capture.wav').write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        delete_analysis_data(tmp_path, plan)
    assert delete_analysis_data(tmp_path, analysis_deletion_plan(tmp_path)) > 0
    assert (tmp_path/'r100_ph0_z0_ir.wav').exists()
    assert (tmp_path/'r100_ph0_z1_ir.wav.blacklisted').exists()
    assert (tmp_path/'review.csv').exists()


def test_native_pane_scans_and_follows_session(qtbot, tmp_path, monkeypatch, review_pools):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from harmonic_drive_qt import project
    from harmonic_drive_qt.measurement_review import MeasurementReviewPane
    folder = tmp_path/'measurement_set'
    folder.mkdir()
    signal = np.zeros(2000, np.float32)
    signal[50] = 1
    wavfile.write(folder/'r100p0_ph0p0_z0p0_ir.wav', 4000, signal)
    monkeypatch.setattr(project, 'get_project_dir', lambda: tmp_path)
    pane = MeasurementReviewPane(SimpleNamespace(is_measurement_set_running=lambda: False))
    qtbot.addWidget(pane)
    pane.resize(1600, 1000)
    pane.show()
    pane.scan()
    qtbot.waitUntil(lambda: not pane.busy, timeout=10000)
    pool = pane._pool
    pane.scan()
    qtbot.waitUntil(lambda: not pane.busy, timeout=10000)
    assert pane._pool is pool
    assert len(review_pools) == 1
    pane.hide()
    assert not pool.closed
    pane.show()
    assert pane._pool is pool
    assert len(pane.rows) == 1
    assert pane.table.rowCount() == 1
    from harmonic_drive_qt.qt_compat import Qt
    pane.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    assert pane.rows[0]['marked']
    report = folder/'measurement_review.json'
    assert 'At least 13 unique valid positions' in report.read_text(encoding='utf-8')
    from harmonic_drive_qt.qt_compat import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    pane.exclude()
    assert (folder/'r100p0_ph0p0_z0p0_ir.wav').exists()
    pane.accept_list()
    qtbot.waitUntil(lambda: not pane.busy, timeout=10000)
    assert (folder/'r100p0_ph0p0_z0p0_ir.wav.blacklisted').exists()
    assert json.loads(report.read_text(encoding='utf-8'))['files'][0]['Status'] == 'Excluded'
    assert 'At least 13 unique valid positions' in report.read_text(encoding='utf-8')
    pane.filter.setCurrentText('Excluded')
    pane._check_all_visible(True)
    pane.restore()
    pane.accept_list()
    qtbot.waitUntil(lambda: not pane.busy, timeout=10000)
    assert (folder/'r100p0_ph0p0_z0p0_ir.wav').exists()
    assert json.loads(report.read_text(encoding='utf-8'))['files'][0]['Status'] == 'Approved capture 1'
    monkeypatch.setattr(project, 'get_project_dir', lambda: tmp_path/'other')
    pane.refresh_context()
    assert pane.rows == []
    assert pane.table.rowCount() == 0
    pane.shutdown()
    assert pool.closed
    assert pane._pool is None


def test_cpu_process_batches_match_serial_fits():
    from scipy.spatial import cKDTree
    from nfs.measurement_review import compare_spatial
    rng = np.random.default_rng(3)
    xyz = rng.normal(size=(129, 3))
    response = np.tile((1 + .01*xyz[:, 0])[:, None], (1, 37)).astype(complex)
    response[20] *= 2
    near = cKDTree(xyz).query(xyz, k=13)[1]
    serial = list(compare_spatial(xyz, response, near, workers=1))
    parallel = list(compare_spatial(xyz, response, near, workers=2))
    assert [row['status'] for row in parallel] == [row['status'] for row in serial]
    np.testing.assert_allclose([row['score'] for row in parallel], [row['score'] for row in serial])
    with pytest.raises(InterruptedError):
        list(compare_spatial(xyz, response, near, cancelled=lambda: True, workers=2))


def test_review_navigation_uses_full_workspace_and_preserves_split(qtbot, tmp_path, monkeypatch, review_pools):
    from harmonic_drive_qt import main_window, project
    from harmonic_drive_qt.qt_compat import QWidget, Signal
    from unittest.mock import Mock
    class DummyPane(QWidget):
        saved = Signal()
        measurement_saved = Signal()
        progress_event = Signal(dict)
        grid_saved = Signal(str, dict)
        def __init__(self, *args, **kwargs):
            super().__init__()
        def set_active(self, value):
            self.active = value
        def refresh_all(self):
            pass
        def on_progress_event(self, event):
            pass
    for name in ('ControlPane', 'AudioSetupPane', 'LiveCapturePane', 'GridGeneratorPane'):
        monkeypatch.setattr(main_window, name, DummyPane)
    monkeypatch.setattr(project, 'get_project_dir', lambda: tmp_path)
    config = tmp_path/'config.ini'
    config.write_text('[app]\nauto_hide_left_menu = False\n')
    backend = Mock()
    backend.is_measurement_set_running.return_value = False
    window = main_window.MainWindow(backend, str(config))
    qtbot.addWidget(window)
    window.show_audio()
    window.show_review()
    pool = window.review_pane._pool
    assert window.workspace.currentWidget() is window.review_pane
    assert window.left_stack.currentWidget() is window.audio_pane
    assert window.menu.parentWidget() is not window.splitter.widget(0)
    window.show_live_capture()
    assert window.workspace.currentWidget() is window.splitter
    assert window.left_stack.currentWidget() is window.audio_pane
    assert 'background: #dbeafe' not in window.review_button.styleSheet()
    assert not pool.closed
    window.show_review()
    assert window.review_pane._pool is pool
    window.close()
    assert pool.closed


@pytest.mark.parametrize(('logical', 'expected'), [(None, 1), (1, 1), (3, 1), (8, 4), (24, 12)])
def test_review_pool_uses_half_logical_processors(monkeypatch, logical, expected):
    import nfs.review_pool as module
    monkeypatch.setattr(module.os, 'cpu_count', lambda: logical)
    assert module.review_worker_count() == expected


def test_warm_pool_reuses_workers_limits_blas_and_accepts_new_scan_data():
    from scipy.spatial import cKDTree
    from nfs.measurement_review import compare_spatial
    from nfs.review_pool import ReviewProcessPool, worker_details
    rng = np.random.default_rng(19)
    xyz = rng.normal(size=(40, 3))
    response = np.tile((1 + .02*xyz[:, 0])[:, None], (1, 37)).astype(complex)
    near = cKDTree(xyz).query(xyz, k=13)[1]
    pool = ReviewProcessPool(workers=2)
    try:
        pool.warm()
        executor = pool._executor
        worker_pids = set(executor._processes)
        assert len(worker_pids) == 2
        details = [future.result(timeout=15) for future in
                   [executor.submit(worker_details) for _ in range(4)]]
        assert all(info['num_threads'] == 1 for _, libraries in details for info in libraries)
        first = list(compare_spatial(xyz, response, near, pool=pool))
        assert all(row['status'] == 'pass' for row in first)
        response[8] *= 2
        second = list(compare_spatial(xyz, response, near, pool=pool))
        assert second[8]['status'] == 'repeat'
        assert pool._executor is executor
        assert set(executor._processes) == worker_pids
        serial = list(compare_spatial(xyz, response, near, workers=1))
        np.testing.assert_allclose([r['score'] for r in second], [r['score'] for r in serial])
    finally:
        pool.close()
    with pytest.raises(InterruptedError, match='closed'):
        pool.warm()


def test_pool_can_close_during_warmup(qtbot):
    import threading
    from nfs.review_pool import ReviewProcessPool
    pool = ReviewProcessPool(workers=2)

    def warm():
        try:
            pool.warm()
        except (InterruptedError, RuntimeError):
            pass

    # Closing startup may cancel an unstarted diagnostic task.
    from concurrent.futures import CancelledError
    errors = []

    def run():
        try:
            warm()
        except CancelledError:
            pass
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    qtbot.waitUntil(lambda: pool._executor is not None)
    with pool._lock:
        processes = list(pool._executor._processes.values())
    pool.close()
    qtbot.waitUntil(lambda: not thread.is_alive(), timeout=15000)
    qtbot.waitUntil(lambda: all(not process.is_alive() for process in processes), timeout=15000)
    assert not errors


def test_closing_pane_cancels_current_scan(qtbot, tmp_path, monkeypatch, review_pools):
    from harmonic_drive_qt import project
    from harmonic_drive_qt import measurement_review as module
    monkeypatch.setattr(project, 'get_project_dir', lambda: tmp_path)
    (tmp_path/'measurement_set').mkdir()
    entered = __import__('threading').Event()

    def blocked_scan(folder, progress, cancelled, **kwargs):
        entered.set()
        assert pane.cancel.wait(5)
        assert cancelled()
        raise InterruptedError('Scan cancelled.')

    monkeypatch.setattr(module, 'scan_directory', blocked_scan)
    pane = module.MeasurementReviewPane(SimpleNamespace(is_measurement_set_running=lambda: False))
    qtbot.addWidget(pane)
    original_cancel = pane.cancel
    pane.scan()
    qtbot.waitUntil(entered.is_set)
    pane.shutdown()
    qtbot.waitUntil(lambda: not pane.worker.is_alive())
    assert pane.cancel.is_set()
    assert pane.cancel is not original_cancel
    pane.shutdown()
