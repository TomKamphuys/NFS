---
sessionId: session-261020-201230-1919
---

# Problem & Solution

### Problem

Two hardcoded `logger.add()` calls create independent log sinks that ignore the configured log level in `config.ini`:

1. **`src/nfs/nfs.py` (line 238-242)** — Adds a file logger at `DEBUG` level for each measurement set to `logs/Scanner.log`.
2. **`src/nfs/plugins/spherical_measurement_points_arcs.py` (line 6)** — Module-level `logger.add()` at `TRACE` level to `scanner.log` (overwrites on import).

### Solution

**1. `nfs.py`**: Replace the hardcoded `level="DEBUG"` with a call to read the configured level from `logging_config`, or simply reuse the existing global logger handlers instead of adding a new sink.

**2. `spherical_measurement_points_arcs.py`**: Remove the module-level `logger.add()` entirely — it appears to be leftover debug code and uses `mode='w'` which overwrites the log file on import.

### Proposed Changes

| File | Change |
|------|--------|
| `src/nfs/nfs.py` | Replace `level="DEBUG"` with `level=None` (inherits root level) or read from config |
| `src/nfs/plugins/spherical_measurement_points_arcs.py` | Remove line 6: `logger.add('scanner.log', mode='w', level="TRACE")` |

# Delivery Steps

### ✓ Step 1: Remove hardcoded logger in plugin
Removed the module-level `logger.add()` call in `spherical_measurement_points_arcs.py` (line 6) that captured everything at TRACE level and overwrote `scanner.log` on import. Also removed the unused `loguru` import.

### ✓ Step 2: Fix measurement set logger level
In `nfs.py`, replaced the hardcoded `level="DEBUG"` in the `logger.add()` call (line 240) with `get_configured_level()` from `logging_config.py` so it respects the configured log level from `config.ini`. Added `_configured_level` module variable and `get_configured_level()` helper to `logging_config.py`.