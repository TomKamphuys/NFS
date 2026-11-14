# Capture detectors

Capture detection lives in `src/nfs/capture_detection`, separately from hardware
capture and DSP processing in `audio.py`. Detectors inspect in-memory data and
return `CaptureIssue(detector, reason, details)` to reject an attempt, or `None`
to accept it. They do not start captures, retry, save files, or move the scanner.

Both `Audio.capture_ir()` and `Audio.preview_ir()` use the same detector runner:

1. Finish acquisition and alignment, then run the `raw` detectors.
2. If accepted, deconvolve and calculate metrics, then run `metrics` detectors.
3. On the first rejection, the existing audio retry loop captures again at the
   same point, up to the configured attempt limit. Saved measurements retain the
   existing failed-capture archiving. Exhaustion returns a failed point.

The wider measurement-set flow still waits for the audio call to return.
Alignment failures remain acquisition exceptions handled by the existing flow.

## Editing or replacing a detector

Each `CaptureDetector` registration defines a name, stage, check callable,
configuration enable key, GUI label, and description. `DEFAULT_DETECTORS` in
`capture_detection/__init__.py` is the explicit built-in registry. Change a check
callable there to replace an algorithm, or add another registration to add a
detector. No changes to capture, deconvolution, or retry code are needed.

```python
from nfs.capture_detection import CaptureContext, CaptureIssue

def check_capture(context: CaptureContext) -> CaptureIssue | None:
    loopback, microphone = context.capture['raw_recordings']
    # Inspect arrays and capture_metadata without modifying them.
    if some_fault_condition(microphone):
        return CaptureIssue('my_detector', 'Microphone fault', {'sample': 1234})
    return None
```

The context supplies sample rate, capture settings, and the complete capture
dictionary, including raw recordings, alignment metadata, and digital references.
At the `metrics` stage it also supplies calculated metrics and the full and
linear IR arrays before export. Arrays are passed by reference; treat all input
data as read-only. Exceptions propagate rather than silently accepting a capture
after a detector error.

For application code or experiments, `Audio(..., detectors=(...))` can supply a
replacement registry. An empty tuple disables all registered checks. There is no
automatic discovery or separate process: plugins are ordinary Python callables.

## GUI and configuration

The built-in registry supplies the detector checkboxes in Advanced sweep
settings, config persistence schema, and audio factory enable-key loading.
Checkbox changes save and reload the backend immediately. PSR/THD threshold and
attempt-limit edits apply when editing finishes.

Existing `retry_check_stream_health`, `retry_check_loopback_silence`, and
`retry_check_mic_silence` keys are retained. `retry_check_psr` and
`retry_check_thd` independently enable the metric gates. Missing switches default
to enabled, preserving behaviour for existing configs. The existing thresholds
remain `retry_psr_threshold` (3.0) and `retry_thd_threshold` (10%). Despite its
name, `max_capture_retries` still specifies total attempts, as before.

This extraction preserves the algorithms, including their limitations: silence
checks inspect only the first repeat and skip entirely silent sweeps; the mic
check retains its existing five-left/four-right window comparison. Algorithm
changes can now be made independently.
