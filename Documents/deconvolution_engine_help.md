# Deconvolution Engine: Capture, Correction and Saved Measurements

This guide describes the updated engine on `feature/deconvolution_engine_v2`.
The existing `audio_help.md` is retained unchanged as a separate guide.

## 1. Hardware routing and the measurement sequence

Use separate speaker and electrical-reference outputs, and separate microphone
and loopback inputs. Connect the reference output directly to the loopback input.
The reference output carries an unprotected marker and sweep; it belongs on the
electrical loopback, not on a loudspeaker.

After the initial driver-settle interval, each repeat plays:

| Stage | Reference output | Speaker output |
|---|---|---|
| Barker marker | Marker | Silence |
| Marker gap | Silence | Silence |
| Sweep | Clean fundamental | Fundamental with configured protection and test harmonics |
| Room decay | Silence | Silence |

The marker gap is fixed at 20 ms in code. Both sweeps start together. The final
capture adds a latency allowance equal to the post-silence interval, giving twice
the post silence after the final sweep so delayed input samples can arrive.

## 2. Sweep generation and deconvolution

Sweep duration includes silent guards at both ends and must be at least 0.1 s.
The engine computes a regularized inverse from the clean digital fundamental.
It retains every calculated inverse sample and uses padded linear convolution
to prevent late energy wrapping into the start of the response.

Farina harmonic separation is retained: harmonic responses precede the linear
response in the full IR. Injected H2/H3 affects only the speaker signal, with
suppression near Nyquist to avoid synthetic aliasing. Neither injected harmonics
nor the loudspeaker protection filter enters the clean digital inverse.

There is no compulsory output high-pass filter. Configured loudspeaker protection
and its optional capped compensation remain available. Finite and continuous
sine tones bypass sweep protection.

## 3. Electrical loopback correction

**Correct audio interface response** enables correction by default. The engine
first deconvolves the electrical sweep, isolates its linear response, and designs
a regularized correction that excludes separated loopback harmonics. It preserves
midband calibration and limits correction gain to 12 dB through 20 kHz, tapering
to 6 dB at Nyquist where that is above 20 kHz.

Only residual bandwidth roll-off is given minimum-phase shaping. The measured
loudspeaker and room response is not converted to minimum phase. Correction
assumes the electrical path represents the interface response to compensate;
differences between microphone and loopback paths remain measurement limitations.

Disable correction for digital-inverse-only measurements. The marker still
provides timing, and the recorded electrical sweep remains available for replay.

## 4. Alignment and room decay

Normalized correlation selects the marker by waveform match rather than signal
level alone. Missing or unreliable markers and incomplete recording windows raise
errors. Alignment uses whole samples, either from the first marker or by finding
each repeat within +/-5 ms of its expected position.

The marker-to-sweep offset is applied equally to both input channels. The engine
does not move the microphone impulse peak to zero: real acoustic delay remains.
Marker clarity retains the raw-correlation PSR scale and ideal-marker reference
used by master. It describes ambiguity, not a probability of correct timing.

**Post silence / room decay** is both the recorded decay interval and the spacing
before the next marker. Choose enough time for the room decay. The whole interval
is retained, with up to a 10 ms fade at the microphone recording's end. The old
`mic_tail_taper_ms` option is accepted but does not set new capture lengths.
Latency allowance is separate and does not extend the processed room response.

The linear export is at least one sweep long and extends for longer requested
decay intervals. Its nominal timing origin is sample 50 (zero-based); hardware
response and acoustic delay can move the actual peak. IR amplitudes retain the
existing sine-RMS convention.

## 5. Saved debug captures

With **Save raw capture for reprocessing** enabled, each saved measurement has:

- A float32 stereo `_capture.wav`, with electrical loopback first and microphone
  second, containing markers, repeats and recorded tails.
- A small `_metrics.json` quality summary.
- A shared `references/<session>/<reference-id>.npz` archive containing the exact
  fundamental, inverse, speaker sweep, reference sweep and marker arrays,
  plus acquisition metadata in a Unicode scalar named `metadata_json`.

A scan shares reference data across its positions. Each standalone measurement
starts a new reference session. Keep the references directory with the raw
captures when moving a project. Unsaved test sweeps do not write these archives.

Load the archive with `np.load(path, allow_pickle=False)` and decode
`json.loads(archive['metadata_json'].item())`. The `shared_reference_v2` schema
records sample rate, raw channel order, repeat count, planned capture layout,
sweep parameters, applied speaker protection HPF and injected harmonic levels.
The archive hash includes this metadata, so changed acquisition settings create
a new reference archive even when the signal arrays are identical.

Regeneration detects markers again from each raw recording using the saved
marker and planned layout. Detected alignment positions and previous processing
choices (correction, fades and export length) are not stored in this metadata;
the regeneration engine decides them afresh. The original inverse and its timing
remain available as reference data, without requiring a new engine to use them.
No per-measurement capture-log JSON sidecar is written. Older reference archives
without `metadata_json` need acquisition details supplied separately.

## 6. Validation and limits

Automated checks cover known responses, separated harmonic levels and timing,
late reflections, full inverse support, correction gain bounds, capture replay,
channel routing, audio ownership and settings persistence. They use synthetic
signals and mocked devices; they do not establish hardware performance.

SNR and aggregate THD remain heuristic summaries. The proposed capture-level
background-noise measurement is not part of this port.
