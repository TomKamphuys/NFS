# Architecture Overview

This page describes how the Near Field Scanner (NFS) software is put together: the
main building blocks, how they depend on each other, and how they collaborate at
run time. It is meant as a *map* to read alongside the auto-generated
[API Reference](api.rst).

The diagrams are written in [Mermaid](https://mermaid.js.org/), which GitHub
renders natively when you view this file in the repository, and which the Sphinx
documentation site renders as well.

## The big picture

NFS drives a motorised arm (a GRBL-controlled machine) that moves a microphone to
a sequence of positions around a loudspeaker and, at every position, plays a sweep
and captures the impulse response (IR). Three concerns are kept strictly separate:

- **Where to measure** — the *measurement points* generators produce the ordered
  list of positions (cylindrical, spherical, file-based, ...).
- **How to move there safely** — the *motion managers* translate the next point
  into concrete, collision-free machine moves and delegate them to the scanner.
- **How to move and how to measure** — the *scanner* (motion) wraps the GRBL
  controller, and the *audio* subsystem plays the sweep and computes the IR.

The `NearFieldScanner` class is the orchestrator that ties these together, and a
set of *factories* build everything from `config.ini`. A small *plugin registry*
lets new generators, motion managers and audio back-ends be added without touching
the core.

```mermaid
flowchart LR
    UI["Qt GUI /<br/>CLI"] --> NFS["NearFieldScanner<br/>(orchestrator)"]
    NFS --> MM["IMotionManager"]
    NFS --> AU["IAudio"]
    NFS --> SC["Scanner"]
    MM --> MP["MeasurementPoints"]
    MM --> SC
    SC --> GRBL["IGrblController"]
    GRBL --> HW["GRBL hardware /<br/>ESP32 (or mock)"]
    AU --> SND["Sound device /<br/>DSP pipeline (or mock)"]

    subgraph Config & Plugins
        CFG["config.ini"]
        REG["registry / factory<br/>+ plugin loader"]
    end
    CFG -.-> NFS
    REG -.-> MP
    REG -.-> MM
    REG -.-> AU
```

## Components

| Component | Module | Responsibility |
|-----------|--------|----------------|
| `NearFieldScanner` | `nfs.nfs` | Orchestrates a full measurement run; owns the scanner, audio and motion manager; handles pausing/stopping, progress reporting and output directories. |
| `Scanner` | `nfs.scanner` | High-level, coordinate-aware motion API (radial/angular/vertical/arc moves) that hides raw G-code; wraps an `IGrblController`. |
| `IGrblController` | `nfs.grbl_controller` | Low-level GRBL transport. Real implementation `ESP32Duino`; `GrblControllerMock` / `GrblControllerMockSimulatedDRO` for tests and offline runs. |
| `IMotionManager` | `nfs.motion_manager` | Turns the next measurement point into safe machine moves. Variants: `CylindricalMeasurementMotionManager`, `FastCylindricalMeasurementMotionManager`, `SphericalMeasurementMotionManager`. |
| `MeasurementPoints` | `nfs.measurement_points` + `nfs.plugins.*` | Protocol producing the ordered sequence of positions to visit. Implemented by the point-generator plugins. |
| `IAudio` | `nfs.audio` | Plays the sweep, captures and computes the impulse response, and plays test sines. Real `Audio`, plus `MockInterfaceAudio` / `AudioMock`. |
| `registry` / `factory` / `loader` | `nfs.registry`, `nfs.factory`, `nfs.loader` | Plugin registration (measurement points, motion managers, audio) and dynamic loading via entry points / config. |
| `CylindricalPosition`, geometry/DSP utils | `nfs.datatypes`, `nfs.utils.*` | Shared value types and helper functions. |

## Class relationships

The following class diagram focuses on the structural relationships (interfaces,
implementations and ownership) rather than every method.

```mermaid
classDiagram
    direction LR

    class NearFieldScanner {
        +take_single_measurement()
        +take_measurement_set(name, overwrite, progress_callback)
        +pause_measurement_set()
        +resume_measurement_set()
        +stop_measurement_set()
        +play_sine(frequency, level, duration)
        +shutdown()
    }

    class Scanner {
        +move_to(r, angle, z)
        +radial_move_to(r)
        +angular_move_to(angle)
        +vertical_move_to(z)
        +cw_arc_move_to(r, z, radius)
        +get_position() CylindricalPosition
        +home()
    }

    class IGrblController {
        <<interface>>
        +send(message)
        +send_and_wait_for_move_ready(message)
        +get_position()
        +get_state()
    }
    class ESP32Duino
    class GrblControllerMock
    class GrblControllerMockSimulatedDRO

    class IMotionManager {
        <<interface>>
        +move_to_safe_starting_radius()
        +next()
        +ready() bool
        +reset()
        +total_points() int
    }
    class CylindricalMeasurementMotionManager
    class FastCylindricalMeasurementMotionManager
    class SphericalMeasurementMotionManager

    class MeasurementPoints {
        <<protocol>>
        +next() CylindricalPosition
        +ready() bool
        +reset()
        +total_points() int
    }

    class CylindricalNoFlyZone {
        +blocks_move(start, end) bool
        +retract_radius float
    }
    class SphericalNoFlyZone {
        +blocks_move(start, end) bool
        +retract_radius float
    }

    class IAudio {
        <<interface>>
        +measure_ir(position, order_id, save)
        +play_sine(frequency, level, duration)
        +stop_sine()
    }
    class Audio
    class MockInterfaceAudio
    class AudioMock

    IGrblController <|.. ESP32Duino
    IGrblController <|.. GrblControllerMock
    IGrblController <|.. GrblControllerMockSimulatedDRO

    IMotionManager <|.. CylindricalMeasurementMotionManager
    IMotionManager <|.. FastCylindricalMeasurementMotionManager
    IMotionManager <|.. SphericalMeasurementMotionManager

    IAudio <|.. Audio
    Audio <|-- MockInterfaceAudio
    IAudio <|.. AudioMock

    NearFieldScanner o--> Scanner
    NearFieldScanner o--> IMotionManager
    NearFieldScanner o--> IAudio
    Scanner o--> IGrblController
    IMotionManager o--> Scanner
    IMotionManager o--> MeasurementPoints
    CylindricalMeasurementMotionManager o--> CylindricalNoFlyZone
    FastCylindricalMeasurementMotionManager o--> CylindricalNoFlyZone
    SphericalMeasurementMotionManager o--> SphericalNoFlyZone
```

### Building the object graph (factories & plugins)

Everything is constructed from `config.ini` by dedicated factories, so the rest of
the code depends only on the interfaces above:

- `NearFieldScannerFactory.create()` wires the whole graph together.
- `ScannerFactory` / `GrblControllerFactory` build the motion stack.
- `MotionManagerFactory` builds the motion manager *and* its `MeasurementPoints`
  (via `nfs.factory.create`).
- `AudioFactory` builds the audio pipeline.

New generators, motion managers and audio back-ends are discovered through the
plugin `registry`, populated by `nfs.loader` from Python *entry points*
(see `pyproject.toml`) or from a config section. This is why adding a plugin
requires no changes to the core classes.

```mermaid
classDiagram
    direction LR
    class NearFieldScannerFactory
    class ScannerFactory
    class GrblControllerFactory
    class MotionManagerFactory
    class AudioFactory
    class Registry {
        +register(name, component)
        +get(name)
        +list()
    }
    class loader {
        +load_plugins(config_file, section)
    }

    NearFieldScannerFactory ..> ScannerFactory
    NearFieldScannerFactory ..> MotionManagerFactory
    NearFieldScannerFactory ..> AudioFactory
    ScannerFactory ..> GrblControllerFactory
    MotionManagerFactory ..> Registry : looks up motion managers
    AudioFactory ..> Registry : looks up audio back-ends
    loader ..> Registry : registers plugins
```

## Runtime behaviour

### Constructing a scanner

```mermaid
sequenceDiagram
    autonumber
    participant App as GUI / CLI
    participant NFSF as NearFieldScannerFactory
    participant SF as ScannerFactory
    participant GF as GrblControllerFactory
    participant MMF as MotionManagerFactory
    participant AF as AudioFactory
    participant Reg as registry / loader

    App->>Reg: load_plugins(config.ini)
    App->>SF: create(config.ini)
    SF->>GF: create(section, config.ini)
    GF-->>SF: IGrblController
    SF-->>App: Scanner
    App->>NFSF: create(scanner, config.ini)
    NFSF->>MMF: create(config.ini, section, scanner)
    MMF->>Reg: build MeasurementPoints + motion manager
    MMF-->>NFSF: IMotionManager
    NFSF->>AF: create(config.ini)
    AF-->>NFSF: IAudio
    NFSF-->>App: NearFieldScanner
```

### Running a full measurement set

This is the core loop of `NearFieldScanner.take_measurement_set()`. The motion
manager decides *how* to reach each point safely (see
[Fast Cylindrical Motion Manager](fast_cylindrical_motion.md) for the fast path),
while the audio subsystem measures the IR once the arm is in position.

```mermaid
sequenceDiagram
    autonumber
    participant App as GUI / CLI
    participant NFS as NearFieldScanner
    participant MM as IMotionManager
    participant MP as MeasurementPoints
    participant SC as Scanner
    participant GRBL as IGrblController
    participant AU as IAudio

    App->>NFS: take_measurement_set(name, progress_callback)
    NFS->>MM: move_to_safe_starting_radius()
    MM->>SC: radial move to safe radius
    SC->>GRBL: send G-code
    NFS->>MM: total_points()

    loop until MM.ready()
        NFS->>NFS: wait while paused / check stop
        NFS->>MM: next()
        MM->>MP: next() / need_to_do_evasive_move()
        MP-->>MM: CylindricalPosition
        MM->>SC: move commands (radial / angular / vertical / arc)
        SC->>GRBL: send_and_wait_for_move_ready(...)
        NFS->>SC: get_position()
        SC-->>NFS: CylindricalPosition
        NFS->>NFS: append position to CSV
        NFS->>AU: measure_ir(position)
        AU-->>NFS: IR saved
        NFS-->>App: progress_callback("point_complete", i/total, ETA)
    end

    NFS->>MM: reset() + move_to_safe_starting_radius()
    NFS->>SC: angular_move_to(0)
    NFS-->>App: progress_callback("finished")
```

### Measuring an impulse response

At each point, `Audio.measure_ir()` runs the DSP pipeline: generate a sweep,
play/record it, align and average repeated sweeps, then deconvolve to obtain the
impulse response.

```mermaid
sequenceDiagram
    autonumber
    participant NFS as NearFieldScanner
    participant AU as Audio
    participant SG as SweepGenerator
    participant AE as AlignmentEngine
    participant DE as DeconvolutionEngine

    NFS->>AU: measure_ir(position)
    AU->>SG: generate() sweep + inverse
    AU->>AU: play sweep & record mic + loopback
    AU->>AE: sync_and_average(recordings)
    AE-->>AU: aligned, averaged response
    AU->>DE: process_ir(mic, inverse, protection_filter)
    DE-->>AU: impulse response
    AU->>AU: save WAV (+ metadata)
    AU-->>NFS: done
```

## Extending the system

To add a new **measurement-point generator**, implement the `MeasurementPoints`
protocol (`nfs.measurement_points`) and expose a `register(factory)` function,
then declare it under the `nfs.measurement_points` entry-point group in
`pyproject.toml` (existing plugins live in `src/nfs/plugins/`).

New **motion managers** implement `IMotionManager` and are registered under
`nfs.motion_managers`; new **audio back-ends** implement `IAudio` and are
registered under `nfs.audio`. Because the core depends only on these interfaces
and the factories, no orchestration code needs to change.
