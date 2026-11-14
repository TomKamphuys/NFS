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
