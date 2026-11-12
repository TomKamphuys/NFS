---
sessionId: session-261111-182912-1l8q
---

# Requirements

### Overview
Add automatic dropout detection and retry to `capture_ir` in `audio.py`. Currently, if a mid-sweep dropout occurs (silence gap due to ASIO buffer underrun), the corrupted data is saved as a valid measurement. The fix adds a **layered defense**: immediate stream-status check after capture, then a post-deconvolution quality gate, with automatic retries up to 3 attempts.

**Important distinction**: The system captures two channels — an electrical loopback (direct wire from output to input, always strong signal) and a microphone (acoustic, can be weak or noisy at certain positions). Dropout detection should rely on the **loopback** channel, not the microphone. The microphone can legitimately have low SNR at some measurement points (e.g., behind the speaker, in nulls, or far from the DUT), so SNR from the mic is **not** a reliable dropout indicator. Instead, the loopback signal and stream health flags are the reliable indicators.

### Scope

**In Scope:**
- Detect stream underflow/overflow flags (`stream_status`) after `_run_sweep()` and retry immediately if found.
- Detect silence gaps in the raw **loopback** recording (dropout signature) and retry. The loopback is the reliable channel — it always has strong signal.
- After deconvolution, check **PSR** (alignment quality from loopback marker) as the quality gate. PSR reflects capture integrity, not acoustic signal strength.
- **SNR is excluded** from the quality gate — the mic can legitimately have low SNR at certain positions. A dropout would show in the loopback/PSR, not the mic SNR.
- After 3 failed attempts, log the failure and return a failure result (do not save corrupted data).
- Add config options for retry count and quality thresholds.
- Apply retry logic to `capture_ir` (used by `measure_ir`). `preview_ir` gets the same protection.

**Out of Scope:**
- Increasing blocksize to prevent dropouts (user can adjust config manually).
- Persistent stream model (discarded earlier).
- Changes to `MockInterfaceAudio` (no dropouts in simulation).

### Acceptance Criteria
1. When `stream_status` contains underflow flags, the measurement retries without running deconvolution.
2. When the raw **loopback** recording contains a silence gap (energy drop > 60 dB for > 10 ms), the measurement retries.
3. After deconvolution, if **PSR < 3.0**, the measurement retries. SNR is not used as a retry trigger.
4. After 3 retries, the measurement fails with a clear log message and does not save files.
5. Retry count and thresholds are configurable in `config.ini` under `[sweep]`.

# Technical Design

### Current Implementation

**`capture_ir`** (line 2232-2312) is the main entry point. It calls:
1. `_run_sweep()` → opens ASIO stream, plays sweep, records, closes stream, returns aligned result.
2. `_process_capture(result)` → deconvolution, returns `ir_full` and `ir_linear`.
3. `verifier.calculate_metrics()` → computes SNR, THD, PSR.
4. `verifier.verify(metrics)` → returns warnings list (but never acts on them).
5. Saves IR files and returns metadata.

**`_run_sweep`** (line 2083-2198) collects `stream_status` in a list inside the callback (line 2137-2138). After the stream closes, it stores `stream_status` in `prepared['timing']['stream_status']` (line 2191). This list is **never inspected** — it's only stored in metadata.

**`preview_ir`** (line 2320-2347) follows the same pattern but without saving.

### Proposed Changes

#### 1. New helper: `_check_stream_health(result)`
New private method on the `Audio` class. Inspects the result of `_run_sweep()` for early signs of dropout:

- **Stream status check**: Iterates `result['capture_metadata'].get('stream_status', [])` for any entry containing 'Underflow' or 'Overflow' in the status string. Returns `True` (unhealthy) if found.

Returns `False` if the capture looks clean.

#### 2. New helper: `_check_silence_gaps(result)`
New private method. Scans the raw **loopback** recording (`result['raw_recordings'][0]`) for silence gaps indicating a dropout:

- Compute RMS energy in sliding windows (e.g., 10 ms windows) across the active sweep region (excluding pre-silence and post-silence).
- If any window has energy below -60 dB relative to the peak energy in the recording, a dropout is detected.
- The loopback is used because it always has strong signal — a silence gap in the loopback is always a dropout, never a legitimate low-signal condition.

Returns `True` if a silence gap is found (needs retry), `False` if clean.

#### 3. New helper: `_check_quality(metrics)`
New private method. Checks post-deconvolution **PSR** only:
- PSR < 3.0 → fails (retry needed)

**SNR is intentionally excluded** — the microphone can legitimately have low SNR at certain positions (behind the speaker, in nulls). PSR from the loopback marker alignment is the reliable quality indicator: a dropout corrupts the marker alignment, lowering PSR regardless of mic signal strength.

Returns `True` if quality is below threshold (needs retry).

#### 3. Retry loop in `capture_ir`
Wrap the capture + processing in a `for attempt in range(max_retries)` loop:

```
for attempt in range(max_retries):
    result = self._run_sweep()
    
    # Layer 1: immediate stream health check (cheap, no deconvolution)
    if self._check_stream_health(result):
        logger.warning(f"Dropout detected (stream status), retry {attempt+1}/{max_retries}")
        continue
    
    # Layer 2: silence gap check (cheap, no deconvolution)
    if self._check_silence_gaps(result):
        logger.warning(f"Dropout detected (silence gap), retry {attempt+1}/{max_retries}")
        continue
    
    # Deconvolution + quality gate
    ir_full, ir_linear = self._process_capture(result)
    metrics = self.verifier.calculate_metrics(...)
    
    if self._check_quality(metrics):
        logger.warning(f"Quality below threshold, retry {attempt+1}/{max_retries}")
        continue
    
    # Success: proceed to save
    break
else:
    # All retries exhausted
    logger.error("Measurement failed after {max_retries} retries, skipping")
    return failure_result
```

#### 4. Config options
Add to `[sweep]` section in `config.ini`:
- `max_capture_retries = 3` — maximum retry attempts per measurement point
- `retry_psr_threshold = 3.0` — minimum PSR for quality gate (loopback-based, not affected by mic position)

Read in `AudioFactory` and passed to `Audio` via `capture_config`.

#### 5. `preview_ir` gets the same protection
Apply the same retry loop to `preview_ir` so preview captures also retry on dropout.

### File Structure
- **Modified**: `src/nfs/audio.py` — add `_check_stream_health`, `_check_silence_gaps`, `_check_quality`, modify `capture_ir` and `preview_ir`.
- **Modified**: `config.ini` — add retry config options.
- **Modified**: `config_default.ini` — add default retry options.

# Delivery Steps

### ✓ Step 1: Add dropout detection helpers and config options
Add three new private methods to the `Audio` class and config options for retry thresholds.
- Add `_check_stream_health(result)` — inspects `stream_status` for underflow/overflow flags.
- Add `_check_silence_gaps(result)` — scans raw **loopback** recording (`result['raw_recordings'][0]`) for silence gaps inside the sweep region. The loopback is used because it always has strong signal — a gap is always a dropout.
- Add `_check_quality(metrics)` — checks **PSR only** against configurable threshold. SNR is excluded because the mic can legitimately have low SNR at certain positions.
- Add `max_capture_retries` and `retry_psr_threshold` to `[sweep]` config.
- Update `AudioFactory` to read new config options into `capture_config`.

### ✓ Step 2: Add retry loop to capture_ir and preview_ir
Wrap the capture+processing in `capture_ir` and `preview_ir` with a retry loop.
- In `capture_ir`: wrap `_run_sweep()`, health checks, `_process_capture()`, and quality gate in a `for attempt in range(max_retries)` loop.
- On dropout detected (layer 1 or 2), retry immediately without deconvolution.
- On quality failure (layer 3), retry with full deconvolution.
- On all retries exhausted, log error and return a failure dict (do not save files).
- Apply the same retry loop to `preview_ir`.
- Ensure debug artifacts are saved only for the final (successful) attempt.

### ✓ Step 3: Add tests and update config defaults
Add unit tests and update default configuration.
- Add tests in `tests/test_audio_capture.py` for:
  - `_check_stream_health` detects underflow flags.
  - `_check_silence_gaps` detects silence gaps in the **loopback** recording.
  - `_check_quality` flags low PSR (SNR is not checked).
  - Retry loop retries on dropout and succeeds on clean capture.
  - Retry loop exhausts retries and returns failure.
- Update `config_default.ini` with new retry options.
- Run existing tests to verify no regressions.

### ✓ Step 4: Handle failed measurements in the scan loop and add color-coded point status
Handle the failure result from `capture_ir` in the measurement loop and add visual point status tracking.
- In `nfs.py`: capture the return value from `measure_ir()`, check for `error` key, log the failure, emit `point_failed` status, and continue to the next point.
- In `audio.py`: add `retries` field to the success result dict so the caller knows how many retries were used.
- In `live_capture.py`: track per-point status (`ok`, `retried`, `failed`) instead of just positions.
- In `matplotlib_plot.py`: color-code points — gray=pending, green=ok, orange=retried, red=failed.
- In `control_pane.py`: handle `point_failed` without triggering the set-level "Failed" UI state.
- Fix existing tests in `test_near_field_scanner.py` to return valid dicts from mocked `measure_ir`.