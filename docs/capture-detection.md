# Capture detectors

Capture detection lives in `src/nfs/capture_detection`, separately from hardware
capture in `audio_capture.py` and DSP processing in `sweep_to_ir.py`.
`audio.py` coordinates these components. Detectors inspect in-memory data and
return `CaptureIssue(detector, reason, details)` to reject an attempt, or `None`
to accept it. Checks do not start captures, retry, save files, or move the scanner.
Stateful detectors may request reference captures through a preparation callback;
audio still owns acquisition and the measurement-set lifecycle.

Both `Audio.capture_ir()` and `Audio.preview_ir()` use the same detector runner:

1. Finish acquisition and run the `raw` detectors on untouched recordings.
2. If accepted, align and average repeats for DSP, deconvolve and calculate
   metrics, then run `metrics` detectors.
3. On the first rejection, the existing audio retry loop captures again at the
   same point, up to the configured attempt limit. Saved measurements retain the
   existing failed-capture archiving. Exhaustion returns a failed point.

The wider measurement-set flow still waits for the audio call to return.
Alignment failures remain acquisition exceptions handled by the existing flow.

## Live detector dialog and scan failure policy

The Qt scan UI supplies a separate detector-event callback. Plugins report
`DetectorEvent` messages through `DetectorSession.report`; they do not import Qt.
The shared dialog renders messages, estimated progress, diagnostics and action
buttons. `DetectorDecision` bridges GUI choices back to the worker without
blocking the UI thread. Stopping a scan or shutting down releases pending waits.
Headless callers omit the callback and retain the existing fail-on-preparation
error behaviour without any dialog delays.

Golden-reference preparation reports acquisition of three single-sweep sequences,
regardless of the averaging count. Progress estimates use their playback length
(markers, gaps, sweeps and silence); completion counts come from finished
acquisitions. Validation has a separate checking state. A successful reference
shows a green check for one second before any scanner movement. If preparation
validation fails, the red result offers **Continue without this detector** or
**Cancel scan**, with diagnostics and a reminder to review Audio Setup. Continue
disables only that detector for this scan; saved settings and other enabled
detectors remain intact. Captures and shared-reference metadata record this
temporary disable. Device/acquisition exceptions still fail the scan; they do
not offer a bypass for a disconnected device.

Clean points do not open the dialog. A suspect capture opens it with the rejection
reason, then reports the replacement attempt and quality-checking state. Accepted
replacements show a green check; exhausted attempts show a red result. Final
results remain visible for one second. No more than `max_capture_retries` total
attempts are made, including the initial attempt.

The Audio Setup toggle **Stop scan when capture attempts are exhausted** saves
`[sweep] stop_on_capture_failure`. It defaults to **False**: failed points are
marked and skipped, then the scan continues. When True, the failed point is
marked and the scan stops using the existing reset/park path. This setting does
not change how many attempts are allowed. Cancellation during reference setup
never triggers the initial move or parking.

The golden loopback detector locates every raw repeat's marker and applies
fractional alignment against its measured reference anchor solely for comparison.
Tom's gap detectors use a marker-only lookup to locate the first active sweep;
they share that lookup within a raw assessment. None of these checks shifts or
averages the arrays destined for DSP. Only after all raw checks pass does audio
align both channels and average repeats for deconvolution. This preserves DSP
timing while preventing averaging from concealing a fault in an individual repeat.
An unusable marker may be rejected by the golden detector; if it is disabled,
the subsequent DSP alignment still reports alignment errors through the retry loop.

## Editing or replacing a detector

Each `CaptureDetector` registration defines a name, stage, check callable,
configuration enable key/default, GUI group/label, and description. An optional
`prepare(DetectorSession)` callable returns state for one measurement set. An
optional `archive(state)` callable supplies uniquely named NumPy arrays for the
existing shared reference NPZ. The plugin owns serialization; audio owns saving.
`DEFAULT_DETECTORS` in
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
dictionary, including raw recordings, acquisition timing and digital references.
Detected DSP alignment positions become available only after raw checks pass.
At the `metrics` stage it also supplies calculated metrics and the full and
linear IR arrays before export. Arrays are passed by reference; treat all input
data as read-only. Exceptions propagate rather than silently accepting a capture
after a detector error.

Before any scanner movement, including the initial safe-radius move,
`Audio.begin_measurement_set()` runs enabled preparation
hooks. `DetectorSession.acquire()` requests an acquisition with configured repeats;
`acquire(num_sweeps=1)` requests a temporary single-sweep capture. Audio restores
the measurement settings even on failure and checks pause/stop requests around
each acquisition.
The returned state is available in `CaptureContext.detector_state`, keyed by
detector name. `measurement_set` distinguishes scans from standalone captures.
State is published only after all preparation succeeds and cleared when the set
ends, fails, stops, or the session directory changes. No reference is reused
across sets. A preparation error fails the set before measuring any points.

For application code or experiments, `Audio(..., detectors=(...))` can supply a
replacement registry. An empty tuple disables all registered checks. There is no
automatic discovery or separate process: plugins are ordinary Python callables.

## GUI and configuration

The built-in registry supplies the detector checkboxes in the audio setup pane, config persistence schema, and audio factory enable-key loading.
Checkbox changes save and reload the backend immediately. PSR/THD threshold and
attempt-limit edits apply when editing finishes.

Driver underflow and overflow checks run on every sweep, including previews and golden reference captures. Legacy `retry_check_stream_health` settings are ignored.

Existing `retry_check_loopback_silence` and
`retry_check_mic_silence` keys are retained. `retry_check_psr` and
`retry_check_thd` independently enable the metric gates. Missing switches for
these existing detectors default to enabled, preserving behaviour for existing configs.
They are grouped in the GUI under **Tom's detectors** in project-level Advanced sweep settings. The existing thresholds
remain `retry_psr_threshold` (3.0) and `retry_thd_threshold` (10%). Despite its
name, `max_capture_retries` still specifies total attempts, as before.

This extraction preserves the algorithms, including their limitations: silence
checks inspect only the first repeat and skip entirely silent sweeps; the mic
check retains its existing five-left/four-right window comparison. Algorithm
changes can now be made independently.

## Experimental golden loopback reference

Enable **Check golden loopback reference** in system-level Advanced audio settings, or
set `[sweep] retry_check_loopback_reference = True`. It defaults to **False** and
applies only to measurement sets. Other detectors remain independently selectable;
enabling this detector does not turn off Tom's detectors.

Before the first measurement point, the plugin requests exactly three independent
single-sweep captures, even if measurements average two or more sweeps. Method v5
uses the marker-aligned null test with one reusable section and the measured
frequency-dependent residual allowance.
It locates each Barker marker,
refines fractional delay using marker samples only, and applies that delay to
the whole repeat. It does not normalize gain, stretch time, or realign individual
sweep windows. It assesses unaveraged raw repeats before audio prepares any
aligned/averaged arrays for DSP. Startup reference captures also bypass DSP
alignment and averaging entirely.

All three capture pairs must match in both directions. The sample-wise **median**
of the aligned trio becomes one fixed golden section; each initial capture must
also match that reference. A disagreeing trio aborts startup rather than searching
for three favourable captures. Reference creation does not produce point IR files.

For every repeat, comparisons include the marker, gap, sweep and recorded tail,
with the prototype's 16-sample interpolation margin. A 10 ms residual RMS window
advances 5 ms, including an end-anchored final window. A capture fails when any
window exceeds the following allowance, relative to the **measured reference's
active-sweep RMS**, excluding marker, gap, internal sweep guards and recorded tail:

```text
hf_ratio = 10**(6/20) * hypot(0.0027, 0.00000063 * sweep_frequency_hz)
hf_db = 20 * log10(hf_ratio)
allowed_db = max(-40, hf_db) + max(4 - abs(hf_db + 40), 0)**2 / 16
allowed_ratio = 10**(allowed_db / 20)
allowed_residual_rms = reference_active_sweep_rms * allowed_ratio
```

The main threshold is 1% (-40 dB). A compact quadratic smooth maximum joins
this floor to the existing HF curve with a 4 dB transition half-width and continuous slope. The transition spans
approximately 2.6-11.9 kHz; above that the HF curve is unchanged, including all
frequencies above 12 kHz. The allowance is about 1.38% at 10 kHz,
2.57% at 20 kHz and 5.06% at 40 kHz. The 10 ms residual measurement is unchanged;
no smoothing, gain fitting or extra timing correction was introduced.
Changing marker energy or adding quiet tail no longer changes the
normalizer. The RMS is always measured from the fixed reference, not the capture
under test; a capture's gain change is not normalized away.
It does not follow the local waveform amplitude or divide by a zero crossing.
For windows overlapping the sweep, including its internal guards, frequency is
evaluated at the window centre and clamped to the active sweep's endpoints.
Marker/gap/tail windows that do not overlap the sweep use just the floor. This
preserves an HF allowance for filtered sweep-end energy in straddling windows
without giving that allowance to the rest of the quiet tail. `residual_thresholds`
is shared by detector decisions and the measurement tools.

Every measurement repeat uses that same reference section and its own marker
alignment. The actual measurement layout still determines expected length and
repeat count. Driver underflow/overflow flags, missing markers, non-finite data,
truncation and changed per-sweep settings also reject the capture. Rejections during
measurement use the existing point retry policy; the golden reference stays fixed.

The reference is held in memory for the set and, when raw/debug capture saving is
enabled, saved in the **existing shared reference NPZ** alongside the digital
references. Successful points and failed attempts link to that same archive.
The added `loopback_reference_*` fields contain the measured waveform, measured
marker anchor, digital marker, and JSON with layout, residual limits, method
version, median aggregation and bootstrap pair results. These fields participate
in the archive's content hash. Arrays are lossless and load without pickle;
existing digital fields and the `shared_reference_v2` schema remain compatible.
New references have `loopback_reference_v5` metadata with one section and the
threshold method, RMS normalization, floor, minimum RMS ratio, frequency coefficient and margin
saved in `limits`. The loader also supports v1 per-repeat and v2/v3/v4 single-sweep
archives. V1/v2 archives without an explicit threshold method replay the original
3%-of-local / 0.5%-of-whole rule. V3 archives retain their frequency curve and
whole-reference normalization. V4 archives keep the earlier approximately 0.54%
floor. Their historical pass/fail decisions are preserved.

For later waveform checks, load the optional reference through the plugin helper:

```python
import numpy as np
import soundfile as sf
from nfs.capture_detection.loopback_reference import (
    Capture, load_loopback_reference, check_capture,
)

with np.load(reference_npz_path, allow_pickle=False) as saved:
    golden = load_loopback_reference(saved)
raw, fs = sf.read(raw_capture_wav_path, dtype='float64', always_2d=True)
assert fs == golden.reference.layout.fs
# Supply the known measurement repeat count; do not infer it from a truncated file.
layout = golden.reference.layout.with_repeats(configured_repeat_count)
result = check_capture(Capture(raw[:, 0]), golden.reference, golden.marker, layout)
```

This example checks the recorded waveform. To also recheck driver events, supply
them as `Capture(raw[:, 0], driver_events)` using the recording's saved metadata.
Older archives and sessions with this detector disabled have no golden-reference
fields; check for `loopback_reference_metadata_json` before loading one.

Standalone measurements and preview sweeps skip this detector and do not acquire
a reference. The limits are experimental, based on the RME Babyface, MOTU M4 and
Scarlett 2i2 repeatability studies plus the requested additional 6 dB margin: matching
captures establish repeatability, not absolute correctness. Shared systematic
faults and deviations below the limits can pass; gain changes or independent
clock drift can cause rejections. This detector assesses the electrical loopback,
not the microphone signal.

The threshold was informed by local hardware repeatability studies. Increased
allowance reduces accidental retries but also reduces sensitivity; these normal
runs do not establish a universal fault-detection rate. Experimental capture tools,
plots and raw evidence are maintained separately from the application repository.
