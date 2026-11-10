import nfs
import pytest

from harmonic_drive_qt import app_version


@pytest.mark.parametrize("embedded_version", ["0.4.3", "1.2.3"])
def test_frozen_version_overrides_stale_package_metadata(monkeypatch, embedded_version):
    monkeypatch.setattr(nfs, "__frozen_version__", embedded_version, raising=False)
    monkeypatch.setattr(app_version, "_get_package_version", lambda _name: "0.2.1")

    assert app_version.get_nfs_version() == embedded_version


def test_frozen_version_is_used_when_package_metadata_is_missing(monkeypatch):
    monkeypatch.setattr(nfs, "__frozen_version__", "0.4.3", raising=False)

    def missing_package(_name):
        raise app_version.PackageNotFoundError

    monkeypatch.setattr(app_version, "_get_package_version", missing_package)

    assert app_version.get_nfs_version() == "0.4.3"


def test_non_frozen_version_uses_package_metadata(monkeypatch):
    monkeypatch.delattr(nfs, "__frozen_version__", raising=False)
    monkeypatch.setattr(app_version, "_get_package_version", lambda _name: "0.4.3")

    assert app_version.get_nfs_version() == "0.4.3"
