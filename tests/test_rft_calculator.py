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
    assert data['scanner_settings']['reflection_free_time_ms'] == 7.25
    config.write_text('[scanner]\nreflection_free_time_ms=1\nfeed_rate=2000\n')
    project.set_project_dir(tmp_path / 'project', str(config))
    project.apply_to_config(str(config))
    parser = configparser.ConfigParser()
    parser.read(config)
    assert parser.getfloat('scanner', 'reflection_free_time_ms') == 7.25
    assert parser.getint('scanner', 'feed_rate') == 2000
