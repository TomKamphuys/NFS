# Audio components

`audio.py` is the measurement coordinator: it owns the public audio interface,
detector selection, retries, output paths, debug archives and IR saving.

The numerical and device implementations live in separate modules:

- `audio_signals.py`: Barker markers, sweep and matched inverse generation,
  test harmonics, playback protection filters and shared amplitude conventions.
- `audio_capture.py`: `AudioCapture`, sweep playback preparation, raw recording,
  device discovery, tone streams, COM ownership and live metering.
- `sweep_to_ir.py`: `SweepToIR`, marker alignment, repeat averaging,
  deconvolution, linear loopback correction, IR export shaping and metrics.

The hardware child process constructs `AudioCapture` directly. It does not
construct the measurement coordinator or a deconvolution pipeline. Conversely,
`sweep_to_ir` imports neither hardware capture nor the PortAudio backend, allowing
recorded arrays to be processed without opening an audio device.

`Audio.capture_engine` and `Audio.ir_processor` own the implementations; existing
`Audio` methods delegate to them. Shared configuration dictionaries and numerical
dependencies preserve existing factory injection. Legacy imports such as
`from nfs.audio import DeconvolutionEngine` re-export the same classes so callers
can migrate independently. This extraction preserves signal mathematics, capture
timing, retry policy and output formats.

## Detector integration

The measurement flow is now raw acquisition → raw detectors → IR alignment and
repeat averaging → deconvolution → metric detectors → saving. Raw rejection
skips IR preparation entirely. The golden-reference plugin requests its three
single-sweep startup sequences through the capture component and keeps its own fractional
marker alignment solely for waveform assessment. Detector state serialization
adds the measured reference to the existing shared NPZ archive.

## Live validation

On 14 November 2026, the integrated path was exercised on the MOTU M4 (MOTU M
Series ASIO), at 48 kHz and −16 dBFS, using two 1.5 s sweeps per acquisition.
Three startup captures established the reference, followed by three independent
holdouts. All holdouts passed without retries; their worst residual scores were
0.0181, 0.0359 and 0.0348 against the rejection boundary of 1.0.

The golden reference was restored from the shared NPZ. Reprocessing every saved
raw holdout through `SweepToIR` reproduced both full and linear IR arrays
bit-for-bit. The test used a separate configuration and did not change the main
config. The raw evidence is retained separately with the local audio-development experiments.
This is a short live check; injected-fault regression tests provide the negative
controls.

Method v2 introduced three single-sweep captures to build one median template,
independently of the measurement averaging count. On 14 November 2026 a subsequent
normal-residual study compared 12 MOTU holdouts per rate at 48 and 96 kHz, with
1-, 2- and 4-repeat recordings; the 96 kHz study used two explicitly scheduled
warm-up streams. Retrospective analysis also covered 940 supplied recordings
(three reference members, 937 independent comparisons) at 48 kHz and -25 dBFS.
No faults were injected and thresholds were not changed. Detailed bounds and
provenance are retained with the local audio-development experiments.

Method v5 keeps that capture path and applies the -40 dB main residual threshold,
with a smooth transition into the original HF allowance near 12 kHz,
to reference validation and measurement repeats. Its parameters are saved in the
reference NPZ, normalized to active-sweep RMS so marker/tail length does not set
sensitivity. V1/v2/v3/v4 archives retain their historical rule during replay. See
`capture-detection.md` for the formula and window-boundary treatment.

## Live test-sweep display

Successful test sweeps return the original raw loopback/microphone arrays with
their processed IRs in memory, without writing measurement files. Live Capture
shows this preview in its raw, impulse-response and frequency-response panels,
even when the open project already contains saved measurements. The watcher
tracks preview changes as well as file modification times. Changing project
context or starting a saved measurement/scan clears the preview so saved results
can be displayed again.

## Post-scan measurement review

Post Silence is applied once after each sweep, including the final one. New
captures do not append a second post-silence interval for interface latency.
Transport latency uses part of the configured silence: a 500 ms setting and
86 ms transport delay leave about 414 ms of recorded decay after the sweep.
Increase Post Silence if a driver leaves too little room decay/noise-analysis
time or truncates the sweep. The capture still rejects incomplete sweeps and
recordings shorter than their acquisition plan.

Alignment retains the available tail common to all repeats, without padding
unrecorded samples, and archives both requested and retained tail lengths.
The live electrical reference compares through the sweep when there is no
latency flush, so changing tail availability does not create false mismatches.
Older archives retain their original tail-comparison layout during replay.

Saved measurements always retain raw captures and shared references, with a
per-capture JSON sidecar recording timing and the reference association. Legacy
`debug_saves=False` settings no longer disable retention. Measurement Review
performs microphone noise and 12-neighbour LF consistency checks after acquisition;
it does not repeat the live electrical residual analysis. Its explicit cleanup
action can remove analysis data after review while retaining linear IRs and the
review report. See [Measurement Review](measurement-review.md).
