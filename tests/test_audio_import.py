"""Audio module import should not require an available PortAudio backend."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nfs import _audio_backend
import nfs.audio as audio


def test_audio_import_does_not_require_sounddevice():
    project_root = Path(__file__).resolve().parents[1]
    code = (
        f"import sys; sys.path.insert(0, {str(project_root / 'src')!r}); "
        "sys.modules['sounddevice'] = None; import nfs.audio"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_backend_configures_asio_before_loading(monkeypatch):
    import os

    query = lambda: []
    calls = []

    def load(name):
        assert os.environ["SD_ENABLE_ASIO"] == "1"
        calls.append(name)
        return SimpleNamespace(query_devices=query)

    monkeypatch.delenv("SD_ENABLE_ASIO", raising=False)
    monkeypatch.setattr(_audio_backend, "import_module", load)
    with pytest.raises(AttributeError):
        getattr(_audio_backend, "__path__")
    assert calls == []
    assert _audio_backend.query_devices is query
    assert calls == ["sounddevice"]


def test_backend_retries_after_loading_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(ModuleNotFoundError):
        _audio_backend.query_devices()
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(query_devices=lambda: ["device"]))
    assert _audio_backend.query_devices() == ["device"]


def test_discovery_without_backend(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    assert audio.get_devices_and_channels() == {}
    assert audio.get_supported_sample_rates(0, 0) == []
    assert audio.get_supported_sample_rates(None, None) == []
