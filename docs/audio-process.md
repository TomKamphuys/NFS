# Audio acquisition process

Hardware-mode `AudioFactory` instances use a lazily spawned
`nfs-audio-acquisition` process. The Qt backend's existing worker still
orchestrates measurements; it no longer owns the hardware stream. Mock
measurements continue to run locally.

The child owns device discovery, sample-rate probes, PortAudio initialization,
ASIO/COM lifetime, stream operations, playback/capture, and metering. All
hardware commands execute on the same child thread. PortAudio invokes its
audio callback on its own driver thread inside that process. Stream closure
and library termination finish before the child releases COM.

The main application retains sweep generation, scanner control, alignment,
averaging, deconvolution, WAV/reference saving and the UI. The child reuses
the local stream implementation without constructing a DSP pipeline. Direct
`Audio` construction without a transport is retained for local driver tests;
normal factory-created hardware audio uses the process transport.

Each sweep uses a temporary shared-memory allocation containing two playback
and two recording buffers. The parent fills and touches the buffers before
starting acquisition. The command pipe carries the allocation name, hardware
settings and a completion/status reply, not individual audio blocks. After
completion the parent copies the recordings and releases the allocation.
The stream still opens/closes once per measurement, as on master; this change
does not introduce persistent streams.

Meters use a separate shared snapshot published by the child's meter worker.
UI polling never waits for capture completion. Driver warnings are forwarded
to the parent's existing logger while a command is being serviced; during a
continuous tone they may be deferred until the next command. No new per-file
diagnostic artifacts or changes to log-level filtering are introduced here.

Commands are serialized. Dead children, disconnected pipes and response
timeouts produce errors instead of indefinite waits. Failed acquisitions
release shared memory; reload creates a fresh client after a fatal process
failure. Normal shutdown stops playback, closes hardware and joins the child.
A nonresponsive child is terminated after a bounded shutdown wait. Frozen
launchers call `multiprocessing.freeze_support()` before GUI/runtime startup.

`reload_nfs()` now runs on the orchestration worker, stops old audio before
replacement, and rejects replacement while a measurement-set thread is active.
Measurement startup no longer reconstructs the backend twice.

## Validation and limits

`tests/test_audio_process.py` uses real spawned processes with synthetic
devices. It checks exact sample transfer, process/thread ownership, variable
callback sizes through the production stream code, tone playback/level/stop,
nonblocking meter access, exceptions, native-style child exit, timeouts,
shared-memory cleanup and shutdown. No audio or motion hardware is accessed.

This separates acquisition from the main interpreter's GIL. The callback is
still Python and shares the child's GIL with its small command/meter workload.
Windows scheduling, USB and audio-driver faults can still interrupt playback.
The effect on the reported dropout rate requires comparable long runs on the
affected interfaces; passing tests or clean callback flags does not establish
uninterrupted physical audio. A built installer also needs a frozen-process
smoke test before release.
