"""Frozen processes must keep dependency caches inside uninstallable storage."""

import importlib.util
import os
from pathlib import Path
import tempfile

import pytest


@pytest.mark.parametrize("use_local", [True, False])
def test_runtime_paths_are_owned_by_app(tmp_path, monkeypatch, use_local):
    path = Path(__file__).resolve().parents[1] / "packaging/runtime_hooks/nfs_runtime_paths.py"
    spec = importlib.util.spec_from_file_location("nfs_runtime_paths", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if use_local:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    else:
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    for name in ("MPLCONFIGDIR", "PYVISTA_USERDATA_PATH", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(tmp_path / "shared"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "previous-temp"))

    module.configure_runtime_paths()

    root = tmp_path / ("local" if use_local else "roaming") / "HALS Near Field Scanner"
    for name in ("MPLCONFIGDIR", "PYVISTA_USERDATA_PATH", "TEMP", "TMP"):
        actual = Path(os.environ[name])
        assert actual.is_relative_to(root)
        assert actual.is_dir()
    assert Path(tempfile.gettempdir()) == root / "temp"
    assert not (tmp_path / "shared").exists()
