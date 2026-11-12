import importlib.util
from pathlib import Path

import pytest

from harmonic_drive_qt import app_version


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


@pytest.mark.parametrize(
    "actual_version, expected_version, exit_code",
    [("0.4.3", "0.4.3", 0), ("0.2.1", "0.4.3", 1)],
)
def test_version_verification_does_not_prepare_runtime_environment(
    launcher, tmp_path, monkeypatch, actual_version, expected_version, exit_code
):
    module, _bundle = launcher
    data_dir = tmp_path / "user-data"
    monkeypatch.setattr(module, "_user_data_dir", lambda: data_dir)
    monkeypatch.setattr(module.sys, "argv", ["HALS Near Field Scanner.exe", "--verify-version", expected_version])
    monkeypatch.setattr(app_version, "get_nfs_version", lambda: actual_version)

    def unexpected_setup():
        pytest.fail("Version verification must not touch the runtime environment")

    monkeypatch.setattr(module, "_prepare_runtime_environment", unexpected_setup)

    assert module.main() == exit_code
    assert not data_dir.exists()


def test_version_verification_can_report_actual_version(launcher, tmp_path, monkeypatch):
    module, _bundle = launcher
    report_file = tmp_path / "actual-version.txt"
    monkeypatch.setattr(module.sys, "argv", [
        "HALS Near Field Scanner.exe",
        "--verify-version",
        "0.4.3",
        "--report",
        str(report_file),
    ])
    monkeypatch.setattr(app_version, "get_nfs_version", lambda: "0.2.1")
    monkeypatch.setattr(
        module,
        "_prepare_runtime_environment",
        lambda: pytest.fail("Version verification must not touch the runtime environment"),
    )

    assert module.main() == 1
    assert report_file.read_text(encoding="utf-8") == "0.2.1"


def test_spawn_dispatch_precedes_runtime_or_gui_startup(launcher, monkeypatch):
    import multiprocessing
    module, _bundle = launcher
    class SpawnDiverted(Exception):
        pass
    def divert():
        raise SpawnDiverted()
    monkeypatch.setattr(multiprocessing, 'freeze_support', divert)
    monkeypatch.setattr(module, '_prepare_runtime_environment',
                        lambda: pytest.fail('Child must not prepare GUI configuration'))
    with pytest.raises(SpawnDiverted):
        module.main()
