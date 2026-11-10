"""PyInstaller runtime hook that sets the package version at freeze time.

When the application runs inside a PyInstaller bundle, importlib.metadata may
not be able to find the correct version. This hook sets a module-level variable
that the application can fall back to.
"""
import sys

# This variable is set by the spec file at build time via string replacement.
# The spec file generates a copy of this hook with the version baked in.
__version__ = "VERSION_PLACEHOLDER"

# Set the version on the nfs package so importlib.metadata.version('nfs')
# fallbacks can use it.
try:
    import nfs
    nfs.__frozen_version__ = __version__
except ImportError:
    pass
