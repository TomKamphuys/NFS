"""Application version lookup shared by the GUI and frozen launcher."""

from importlib.metadata import PackageNotFoundError, version as _get_package_version
import sys


def get_nfs_version() -> str:
    """Return the build-time version when frozen, otherwise installed metadata."""
    embedded_version = getattr(sys, "_hals_frozen_version", None)
    if isinstance(embedded_version, str):
        return embedded_version

    try:
        return _get_package_version("nfs")
    except PackageNotFoundError:
        return "unknown"
