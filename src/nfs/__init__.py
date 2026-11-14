"""Public scanner exports, loaded only when requested.

Importing a signal or IR-processing component must not construct the scanner's
audio dependency graph. Keep the established package-level names available.
"""

from importlib import import_module

__all__ = ['NearFieldScannerFactory', 'NearFieldScanner', 'ScannerFactory', 'Scanner']


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    source = '.nfs' if name.startswith('NearField') else '.scanner'
    value = getattr(import_module(source, __name__), name)
    globals()[name] = value
    return value
