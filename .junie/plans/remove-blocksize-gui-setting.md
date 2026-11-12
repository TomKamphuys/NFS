---
sessionId: session-261112-164041-9nrp
---

# Requirements

### Overview
Remove the block size GUI setting from the audio setup pane and hardcode the block size to `0` (auto) in the audio engine. The block size should now always match the audio driver's own setting, so the user-facing control is unnecessary and potentially misleading. All `blocksize` references in tests, config schema, and the meter worker will be cleaned up to avoid dead code.

### Changes In Scope
- Remove the block size spinbox widget and its label from the advanced audio settings group in `audio_setup_pane.py`.
- Remove block size from the config save logic in `audio_setup_pane.py`.
- Remove block size from the `commit_controls` list in `audio_setup_pane.py`.
- Add a `BLOCKSIZE = 0` constant in `audio.py` and use it in `AudioFactory` instead of reading from config.
- Remove the `blocksize` entry from the config schema in `config_support.py`.
- Remove `blocksize` parameter from `_AudioMeterWorker` constructor and all call sites in `audio.py`.
- Remove `blocksize` from all test config fixtures and `hw_config` dicts across the test suite.
- Update tests that explicitly test the block size GUI widget.

### Out of Scope
- Nothing. All `blocksize` references will be removed or replaced with hardcoded `0`.

# Technical Design

### Current Implementation
- **`src/harmonic_drive_qt/audio_setup_pane.py`**:
  - Line 326–327: `self.blocksize` spinbox created with default 2048, special value text "Automatic".
  - Line 331–337: Label and spinbox added to advanced audio `QGridLayout` and collapse list.
  - Line 864: `self.blocksize` in `commit_controls` list (triggers config save on `editingFinished`).
  - Line 1093: `("audio", "blocksize"): int(self.blocksize.value())` saved to config.
- **`src/harmonic_drive_qt/config_support.py`**:
  - Lines 150–151: `blocksize` schema entry defined as `int`.
- **`src/nfs/audio.py`**:
  - Line 322: `_AudioMeterWorker.__init__` accepts `blocksize` parameter, used in `max(8192, sample_rate, int(blocksize))` — always shadowed by `sample_rate`.
  - Line 3092: `blocksize` read from config via `_get_required_config` into `hw_config` dict.
  - Lines 2146, 2207, 2613, 2646, 2661, 2705: `self.hw['blocksize']` used in stream creation and meter worker instantiation.
- **Test files**: 31 occurrences of `blocksize` across test fixtures in `test_qt_audio_setup_pane.py`, `test_audio_engine.py`, `test_audio_capture.py`, `test_audio_asio.py`, `test_audio_regression.py`, `test_dsp_verification.py`, `test_dut_volume.py`, `test_mock_spatial_measurements.py`, and `test_spatial_model.py`.

### Proposed Changes

**`src/harmonic_drive_qt/audio_setup_pane.py`:**
- Remove `self.blocksize` spinbox creation (line 326–327).
- Remove `blocksize_label` creation and grid widget additions (lines 331–333).
- Remove `blocksize_label` and `self.blocksize` from the collapse children list (line 337).
- Remove `self.blocksize` from `commit_controls` (line 864).
- Remove `("audio", "blocksize")` from the save values dict (line 1093).

**`src/harmonic_drive_qt/config_support.py`:**
- Remove the `blocksize` schema entry (lines 150–151).

**`src/nfs/audio.py`:**
- Line 3092: Replace `_get_required_config(..., 'blocksize', int)` with a `BLOCKSIZE` constant set to `0`.
- Line 322: Remove `blocksize` parameter from `_AudioMeterWorker.__init__`.
- Line 326: Change `self._capacity = max(8192, self.sample_rate, int(blocksize))` to `self._capacity = max(8192, self.sample_rate)`.
- Lines 2146, 2613, 2661: Remove `blocksize` argument from `_AudioMeterWorker` instantiation calls (pass only `fs`).

**Test files — remove `blocksize` from all config and `hw_config` fixtures:**
- `test_qt_audio_setup_pane.py`: Remove `blocksize` from all config file fixtures (10+ occurrences) and remove the blocksize widget assertions from `test_audio_setup_pane_commit_and_immediate_controls`.
- `test_audio_engine.py`: Remove `blocksize` from config dicts (4 occurrences).
- `test_audio_capture.py`: Remove `blocksize` from `hw_config` and config dicts (2 occurrences).
- `test_audio_asio.py`: Remove `blocksize` from `hw_config` dicts (2 occurrences).
- `test_audio_regression.py`: Remove `blocksize` from config dict.
- `test_dsp_verification.py`: Remove `blocksize` from config dict.
- `test_dut_volume.py`: Remove `blocksize` from `hw_config` dict.
- `test_mock_spatial_measurements.py`: Remove `blocksize` from `hw_config` dict.
- `test_spatial_model.py`: Remove `blocksize` from config dicts (4 occurrences).

# Delivery Steps

### ✓ Step 1: Remove blocksize GUI widget and config logic
Remove the block size spinbox, label, and all related GUI/config logic from `audio_setup_pane.py` and `config_support.py`.
- Remove `self.blocksize` spinbox creation and its special value text.
- Remove `blocksize_label` and grid widget additions from the advanced audio group.
- Remove `blocksize_label` and `self.blocksize` from the collapse children list.
- Remove `self.blocksize` from `commit_controls`.
- Remove `("audio", "blocksize")` from the save values dict in `save_audio_setup()`.
- Remove the `blocksize` schema entry from `config_support.py`.

### ✓ Step 2: Add BLOCKSIZE constant in AudioFactory and simplify meter worker
Replace the config-based blocksize lookup with a `BLOCKSIZE = 0` constant, and remove the unused `blocksize` parameter from `_AudioMeterWorker`.
- In `audio.py`, add a module-level `BLOCKSIZE = 0` constant.
- In `audio.py` line 3092, replace `_get_required_config(..., 'blocksize', int)` with `BLOCKSIZE`.
- Remove `blocksize` parameter from `_AudioMeterWorker.__init__` and simplify `_capacity` calculation.
- Update all `_AudioMeterWorker` instantiation calls (lines 2146, 2613, 2661) to not pass `blocksize`.

### ✓ Step 3: Clean blocksize from all test fixtures
Remove `blocksize` from all test config dicts and `hw_config` fixtures across the test suite, and update the GUI test.
- `test_qt_audio_setup_pane.py`: Remove `blocksize` from all config file fixtures (10+ occurrences) and remove blocksize widget assertions from `test_audio_setup_pane_commit_and_immediate_controls`.
- `test_audio_engine.py`: Remove `blocksize` from config dicts (4 occurrences).
- `test_audio_capture.py`: Remove `blocksize` from `hw_config` and config dicts (2 occurrences).
- `test_audio_asio.py`: Remove `blocksize` from `hw_config` dicts (2 occurrences).
- `test_audio_regression.py`: Remove `blocksize` from config dict.
- `test_dsp_verification.py`: Remove `blocksize` from config dict.
- `test_dut_volume.py`: Remove `blocksize` from `hw_config` dict.
- `test_mock_spatial_measurements.py`: Remove `blocksize` from `hw_config` dict.
- `test_spatial_model.py`: Remove `blocksize` from config dicts (4 occurrences).
- Run the full test suite to verify no regressions.