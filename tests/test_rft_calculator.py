import configparser
import json
import os
import math
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
from harmonic_drive_qt import project
from harmonic_drive_qt.qt_compat import QApplication, QPushButton, QDialog
from harmonic_drive_qt.settings_dialog import SettingsDialog
from rft_calc.dialog import RFTDialog
from rft_calc.rft_calculator import calculate_rft


def test_rft_geometry_and_qt_render():
    app = QApplication.instance() or QApplication([])
    result = calculate_rft(100, 1100, 1100)
    assert result['rft_ms'] == pytest.approx((math.hypot(100, 2200) - 100) / 343)
    dialog = RFTDialog(None)
    assert dialog.diagram.speaker.isValid()
    dialog.show()
    app.processEvents()
    assert not dialog.grab().isNull()
    dialog.fields[1].setValue(0)
    assert dialog.result['rft_ms'] == 0
    dialog.close()


def test_rft_settings_apply_and_project_roundtrip(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(project, '_project_dir', tmp_path / 'project')
    monkeypatch.setattr(project, '_project_data', {'project_name': 'RFT'})
    config = tmp_path / 'config.ini'
    config.write_text('[scanner]\nfeed_rate=1000\n')
    settings = SettingsDialog(str(config), lambda: None)
    field, _ = settings.inputs[('scanner', 'reflection_free_time_ms')]
    button = next(b for b in settings.findChildren(QPushButton) if b.text() == 'RFT Calculator')
    monkeypatch.setattr(RFTDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    button.click()
    assert float(field.text()) == pytest.approx(calculate_rft(50, 1000, 1000)['rft_ms'], abs=0.0001)
    field.setText('7.25')
    settings.save()
    data = json.loads(project.get_project_json_path().read_text())
    assert data['stage1_vars']['fdw_rft_ms'] == '7.25'
    assert 'scanner_settings' not in data
    config.write_text('[scanner]\nreflection_free_time_ms=1\nfeed_rate=2000\n')
    project.set_project_dir(tmp_path / 'project', str(config))
    project.apply_to_config(str(config))
    parser = configparser.ConfigParser()
    parser.read(config)
    assert parser.getfloat('scanner', 'reflection_free_time_ms') == 7.25
    assert parser.getint('scanner', 'feed_rate') == 2000


@pytest.mark.parametrize('stage1_vars, expected', [
    ({'fdw_oct_res': '12'}, '0.0'),
    ({'fdw_oct_res': '12', 'fdw_rft_ms': '8.0'}, '8.0'),
])
def test_rft_save_preserves_stage1_settings(tmp_path, monkeypatch, stage1_vars, expected):
    monkeypatch.setattr(project, '_project_dir', tmp_path)
    monkeypatch.setattr(project, '_project_data', {
        'project_name': 'RFT',
        'stage1_vars': stage1_vars,
    })
    config = tmp_path / 'config.ini'
    config.write_text('[scanner]\nfeed_rate=2000\n')
    project.save_project()
    data = json.loads(project.get_project_json_path().read_text())
    assert data['stage1_vars'] == {'fdw_oct_res': '12', 'fdw_rft_ms': expected}
    assert 'scanner_settings' not in data
    project.apply_to_config(str(config))
    project.sync_rft_from_config(str(config))
    assert project.get_project_data()['stage1_vars']['fdw_oct_res'] == '12'


def test_rft_system_default_and_foreign_project(tmp_path, monkeypatch):
    monkeypatch.setattr(project, '_project_dir', tmp_path)
    monkeypatch.setattr(project, '_project_data', {})
    config = tmp_path / 'config.ini'
    config.write_text('[scanner]\nreflection_free_time_ms=5.0\n')
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'Foreign_project.json').write_text(json.dumps({
        'project_name': 'Foreign', 'stage1_vars': {'fdw_rft_ms': '9.0'},
    }))
    project.set_project_dir(foreign, str(config))
    project.apply_to_config(str(config))
    assert project.get_system_rft_default(str(config)) == 5.0
    project.save_project_to(foreign, 'Foreign', str(config))
    assert json.loads(project.get_project_json_path().read_text())['stage1_vars']['fdw_rft_ms'] == '9.0'
    project.set_project_dir(tmp_path / 'fresh', str(config))
    project.apply_to_config(str(config))
    assert project.get_project_data()['stage1_vars']['fdw_rft_ms'] == '5.0'
    app = QApplication.instance() or QApplication([])
    settings = SettingsDialog(str(config), lambda: None)
    field, _ = settings.inputs[('scanner', 'reflection_free_time_ms')]
    field.setText('6.5')
    button = next(b for b in settings.findChildren(QPushButton) if b.text() == 'Set as system default')
    button.click()
    settings.save()
    assert project.get_system_rft_default(str(config)) == 6.5
    field_settings = SettingsDialog(str(config), lambda: None)
    field_settings.inputs[('scanner', 'reflection_free_time_ms')][0].setText('7.0')
    field_settings.save()
    assert project.get_system_rft_default(str(config)) == 6.5
    project.set_project_dir(tmp_path / 'restart', str(config))
    project.apply_to_config(str(config))
    assert project.get_project_data()['stage1_vars']['fdw_rft_ms'] == '6.5'
