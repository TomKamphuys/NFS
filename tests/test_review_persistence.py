"""A single readable JSON file restores analysis and preserves action history."""
import json
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.io import wavfile

from nfs.review_storage import (REVIEW_REPORT_NAME, dataset_identity,
                               write_review_report, load_review_results)


def saved_row(name='point_ir.wav'):
    return dict(filename=name, status='repeat', marked=True, score=2.,
                reason='LF response differs from nearby positions.',
                primary_issue='spatial_discrepancy',
                issues=[dict(code='spatial_discrepancy', category='Acoustic/spatial',
                             severity='repeat', message='LF response differs from nearby positions.')],
                metrics={'mic_pre_dbfs': -70., 'sweep_regions_s': [[.3, 1.8]]},
                response=np.arange(37)*(.1+.2j), xyz=np.array([.1, .2, .3]),
                cylindrical=(100., 45., 30.), bands=[], neighbours=[],
                identity=(100, 200, 300), coverage='Saved microphone analysis')


def test_single_json_restores_features_and_does_not_change_dataset_identity(tmp_path):
    (tmp_path/'point_ir.wav').write_bytes(b'unchanged audio')
    before = dataset_identity(tmp_path)
    row = saved_row()
    path = write_review_report(tmp_path, [row], identity=before)
    assert path.name == 'measurement_review.json'
    assert set(p.name for p in tmp_path.iterdir()) == {'point_ir.wav', REVIEW_REPORT_NAME}
    assert dataset_identity(tmp_path) == before
    document = json.loads(path.read_text(encoding='utf-8'))
    assert document['files'][0]['Reason'] == row['reason']
    loaded = load_review_results(tmp_path)
    assert loaded['identity'] == before
    restored = loaded['rows'][0]
    np.testing.assert_array_equal(restored['response'], row['response'])
    np.testing.assert_array_equal(restored['xyz'], row['xyz'])
    assert restored['metrics'] == row['metrics']
    assert not restored['marked']
    write_review_report(tmp_path, [row], {'point_ir.wav': 'Excluded'})
    loaded = load_review_results(tmp_path)
    assert loaded['rows'][0]['filename'] == 'point_ir.wav.blacklisted'
    assert loaded['rows'][0]['status'] == 'excluded'
    assert json.loads(path.read_text())['files'][0]['Status'] == 'Excluded'


def test_corrupt_results_are_replaced_by_successful_rescan(tmp_path):
    (tmp_path/REVIEW_REPORT_NAME).write_text('{broken JSON')
    with pytest.raises(ValueError):
        load_review_results(tmp_path)
    write_review_report(tmp_path, [saved_row()], identity=())
    assert load_review_results(tmp_path)['rows'][0]['status'] == 'repeat'


def test_project_reload_restores_results_notice_and_rescan_refreshes(qtbot, tmp_path, monkeypatch):
    from harmonic_drive_qt import project, measurement_review as module

    class Pool:
        workers = 1
        def warm(self):
            pass
        def close(self):
            pass
        def map(self, function, tasks, cancelled=lambda: False):
            for task in tasks:
                yield function(task)

    monkeypatch.setattr(module, 'ReviewProcessPool', Pool)
    folder = tmp_path/'measurement_set'
    folder.mkdir()
    name = 'r100p0_ph0p0_z0p0_ir.wav'
    signal = np.zeros(2000, dtype=np.float32)
    signal[50] = .1
    wavfile.write(folder/name, 4000, signal)
    row = saved_row(name)
    write_review_report(folder, [row], identity=dataset_identity(folder))
    monkeypatch.setattr(project, 'get_project_dir', lambda: tmp_path)
    pane = module.MeasurementReviewPane(SimpleNamespace(is_measurement_set_running=lambda: False))
    qtbot.addWidget(pane)
    assert pane.saved_results
    assert not pane.saved_notice.isHidden()
    assert 're-scan to refresh' in pane.saved_notice.text()
    assert not pane.stale
    assert pane.table.rowCount() == 1
    np.testing.assert_array_equal(pane.rows[0]['response'], row['response'])
    assert not pane.rows[0]['marked']

    # Switching projects cannot leave the previous project's rows or notice behind.
    monkeypatch.setattr(project, 'get_project_dir', lambda: tmp_path/'other')
    pane.refresh_context()
    assert not pane.rows
    assert pane.saved_notice.isHidden()
    monkeypatch.setattr(project, 'get_project_dir', lambda: tmp_path)
    pane.refresh_context()
    assert pane.saved_results and pane.table.rowCount() == 1

    # Changed captures keep the historical result visible but prevent stale actions.
    wavfile.write(folder/name, 4000, np.pad(signal, (0, 20)))
    pane.refresh_context()
    assert pane.stale
    assert 'changed' in pane.saved_notice.text()
    pane.scan()
    qtbot.waitUntil(lambda: not pane.busy, timeout=10000)
    assert not pane.saved_results
    assert not pane.stale
    assert pane.saved_notice.isHidden()
    assert load_review_results(folder)['identity'] == dataset_identity(folder)
    pane.shutdown()


def test_corrupt_project_review_shows_rescan_notice(qtbot, tmp_path, monkeypatch):
    from harmonic_drive_qt import project, measurement_review as module
    folder = tmp_path/'measurement_set'
    folder.mkdir()
    (folder/REVIEW_REPORT_NAME).write_text('not JSON')
    monkeypatch.setattr(project, 'get_project_dir', lambda: tmp_path)
    pane = module.MeasurementReviewPane(SimpleNamespace(is_measurement_set_running=lambda: False))
    qtbot.addWidget(pane)
    assert not pane.rows
    assert not pane.saved_notice.isHidden()
    assert 'could not be loaded' in pane.saved_notice.text()
    assert not pane.table.signalsBlocked()
    pane.shutdown()
