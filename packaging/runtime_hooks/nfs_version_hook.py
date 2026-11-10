"""PyInstaller runtime hook that sets the authoritative build version."""

# This variable is set by the spec file at build time via string replacement.
# The spec file generates a copy of this hook with the version baked in.
__version__ = "VERSION_PLACEHOLDER"

# Set the version on the nfs package so application code does not rely on
# potentially stale package metadata bundled by PyInstaller.
try:
    import nfs
    nfs.__frozen_version__ = __version__
except ImportError:
    pass
