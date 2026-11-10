"""PyInstaller runtime hook that sets the authoritative build version."""

import sys

# This variable is set by the spec file at build time via string replacement.
# The spec file generates a copy of this hook with the version baked in.
__version__ = "VERSION_PLACEHOLDER"

# Do not import the scanner package here: the hook must run even if a package
# dependency is unavailable, and stale bundled metadata must never win.
setattr(sys, "_hals_frozen_version", __version__)
