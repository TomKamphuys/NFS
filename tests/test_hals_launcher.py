import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "packaging" / "hals_launcher.py"
    spec = importlib.util.spec_from_file_location("hals_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(module, "_bundle_dir", lambda: bundle)
    return module, bundle


def test_fresh_launch_seeds_both_files(launcher, tmp_path):
    module, bundle = launcher
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in ("config.ini", "config_default.ini"):
        (bundle / name).write_bytes(name.encode())

    module._seed_config_files(data_dir)

    for name in ("config.ini", "config_default.ini"):
        assert (data_dir / name).read_bytes() == (bundle / name).read_bytes()


def test_repeat_launch_preserves_user_configuration(launcher, tmp_path):
    module, bundle = launcher
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (bundle / "config.ini").write_bytes(b"bundled user settings")
    (bundle / "config_default.ini").write_bytes(b"bundled defaults")
    module._seed_config_files(data_dir)
    user_config = b"[machine]\r\ncustom = preserved\r\n"
    (data_dir / "config.ini").write_bytes(user_config)
    (data_dir / "config_default.ini").write_bytes(b"stale defaults")

    module._seed_config_files(data_dir)

    assert (data_dir / "config.ini").read_bytes() == user_config
    assert (data_dir / "config_default.ini").read_bytes() == b"bundled defaults"


def test_upgrade_refreshes_only_defaults(launcher, tmp_path):
    module, bundle = launcher
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (bundle / "config.ini").write_bytes(b"old bundled settings")
    (bundle / "config_default.ini").write_bytes(b"old defaults")
    module._seed_config_files(data_dir)
    user_config = b"[motion]\r\nsafe_radius = 123\r\n; user comment\r\n"
    (data_dir / "config.ini").write_bytes(user_config)
    (bundle / "config.ini").write_bytes(b"new bundled settings")
    (bundle / "config_default.ini").write_bytes(b"new defaults")

    module._seed_config_files(data_dir)

    assert (data_dir / "config.ini").read_bytes() == user_config
    assert (data_dir / "config_default.ini").read_bytes() == b"new defaults"


def test_missing_bundle_sources_leave_existing_files_untouched(launcher, tmp_path):
    module, _ = launcher
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    module._seed_config_files(data_dir)
    assert list(data_dir.iterdir()) == []
    for name in ("config.ini", "config_default.ini"):
        (data_dir / name).write_bytes(b"existing")

    module._seed_config_files(data_dir)

    for name in ("config.ini", "config_default.ini"):
        assert (data_dir / name).read_bytes() == b"existing"


def test_copy_error_is_non_fatal_and_remaining_file_is_seeded(launcher, tmp_path, monkeypatch):
    module, bundle = launcher
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in ("config.ini", "config_default.ini"):
        (bundle / name).write_bytes(b"bundled")
    copyfile = module.shutil.copyfile

    def copy_with_error(source, target):
        if source.name == "config_default.ini":
            raise OSError("not writable")
        return copyfile(source, target)

    monkeypatch.setattr(module.shutil, "copyfile", copy_with_error)

    module._seed_config_files(data_dir)

    assert not (data_dir / "config_default.ini").exists()
    assert (data_dir / "config.ini").read_bytes() == b"bundled"