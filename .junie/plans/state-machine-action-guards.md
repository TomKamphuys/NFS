---
sessionId: session-261017-183002-1dg1
---

# Requirements

### Overview & Goals
Introduce a centralized **AppState state machine** in `BackendManager` that provides a single source of truth for what the application is doing. When the state changes, `BackendManager` **pushes** the new state to GUI components via a PySide6 signal. GUI components subscribe and react — they never poll for state. This follows the Hollywood principle: "Don't call us, we'll call you."

### Scope

**In Scope:**
- `AppState` enum with states: `IDLE`, `HOMING`, `JOGGING`, `MEASURING_SINGLE`, `MEASURING_SET`, `MEASURING_SET_PAUSED`, `TEST_SWEEP`, `SINE_PLAYING`, `ERROR`.
- State transition method in `BackendManager` with guard logic.
- `state_changed(new_state: AppState)` PySide6 signal emitted on every transition.
- `can_action(action)` method that checks whether a given action is allowed in the current state.
- `ControlPane` subscribes to `state_changed` and updates all button enable/disable in one handler.
- Guard `show_settings()` and `browse_project()` in `MainWindow` against running measurements.
- Guard `generate_and_plan()` in `GridPane` against running measurements.

**Out of Scope:**
- Visual status bar or header indicator (can be added later by subscribing to the same `state_changed` signal).
- Refactoring of `nfs.py` internal state tracking.

### User Stories
1. As a user, when a measurement set is running, I cannot jog the machine so I don't corrupt data or cause collisions.
2. As a user, when homing is in progress, I cannot jog or start a measurement so the machine completes homing safely.
3. As a user, when any measurement is running, I cannot open settings or change the project folder so the measurement completes without interruption.
4. As a user, I can see at a glance which controls are available because disabled controls clearly indicate what's blocked.

# Technical Design

### Current Implementation

**State tracking is scattered across multiple components:**
- `nfs.py`: `_measurement_running` (bool), `_measurement_pause_requested` (threading.Event), `_measurement_stop_requested` (threading.Event)
- `control_pane.py`: `_jog_in_progress` (bool), `sine_running` (bool), `_homing_glow_phase` (float, indicates homing visually)
- `backend.py`: delegates to `nfs.is_measurement_set_running()` and `nfs.is_measurement_set_paused()`
- GUI enable/disable logic in `control_pane.refresh_position()` (line 730-743) checks `loaded and not self._jog_in_progress` for jog buttons, and `loaded` for control buttons
- No guards on `show_settings()`, `browse_project()`, `generate_and_plan()`, `take_single_measurement()`, or `test_sweep()`

**Actions identified:**
| Action | Method | Current Guard |
|---|---|---|
| Jog | `control_pane._run_jog()` | `_jog_in_progress` flag only |
| Home | `control_pane._run_command('home')` | None |
| Measurement Set | `control_pane.toggle_measurement_set()` | `is_measurement_set_running()` check in `backend.start_measurement_set()` |
| Single Measurement | `control_pane.take_single_measurement()` | None |
| Test Sweep | `control_pane.test_sweep()` | None |
| Sine Play | `control_pane.toggle_sine()` | `sine_running` flag only |
| Settings | `main_window.show_settings()` | None |
| Project Browse | `main_window.browse_project()` | None |
| Grid Generate | `grid_pane.generate_and_plan()` | None |

### Key Decisions

**1. State machine lives in `BackendManager`, not in the GUI.**
The backend owns the truth about what's happening and pushes state changes to the GUI. This keeps state logic centralized and testable without a GUI.

**2. Push-based state notifications (Hollywood principle).**
`BackendManager` owns the current `AppState` and emits a `state_changed(new_state: AppState)` PySide6 signal whenever it transitions. GUI components connect to the signal once and react. The GUI never calls `get_app_state()` — it always knows the latest state because the backend told it. This eliminates polling latency and decouples the GUI from the backend's internal timing.

**3. Centralized state computation in `BackendManager`.**
Internally, `BackendManager` still computes the current state from flags (`_jog_in_progress`, `_single_measurement_running`, etc.) and NFS/Scanner status. But instead of exposing a `get_app_state()` method for the GUI to call, it calls an internal `_emit_state_if_changed()` after any flag or operation changes. If the computed state differs from the last emitted state, the signal fires.

**4. Action-based permission checking.**
Instead of each GUI component hard-coding which states block which actions, a `can_action(action_name)` method encodes the conflict matrix centrally. Adding a new action requires updating only this one method.

**5. Existing polling timers remain for data, not state.**
The 250ms `refresh_position()` timer continues to fetch position data. The 500ms `refresh_progress()` timer continues to fetch measurement progress. Neither timer queries app state — they only update position text and progress bars. State-driven GUI updates happen in the `state_changed` handler instead.

### Proposed Changes

#### 1. `backend.py` — Add `AppState` enum and state machine

```python
from enum import Enum, auto

class AppState(Enum):
    IDLE = auto()
    HOMING = auto()
    JOGGING = auto()
    MEASURING_SINGLE = auto()
    MEASURING_SET = auto()
    MEASURING_SET_PAUSED = auto()
    TEST_SWEEP = auto()
    SINE_PLAYING = auto()
    ERROR = auto()
```

Add to `BackendManager.__init__`:
- `_jog_in_progress = False`
- `_single_measurement_running = False`
- `_test_sweep_running = False`
- `_sine_playing = False`

Add signal:
- `state_changed: Signal(AppState)` — emitted whenever the computed app state transitions

Add methods:
- `_compute_state() -> AppState` — internal method that computes current state from flags + NFS + Scanner state
- `_emit_state_if_changed()` — compares `_compute_state()` to `_last_state`; emits `state_changed` if different
- `can_action(action: str) -> bool` — returns whether an action is allowed in the current state
- `set_jog_in_progress(value: bool)` / `is_jogging() -> bool`
- `set_single_measurement_running(value: bool)` / `is_single_measuring() -> bool`
- `set_test_sweep_running(value: bool)` / `is_test_sweeping() -> bool`
- `set_sine_playing(value: bool)` / `is_sine_playing() -> bool`

**State priority** (highest first, for `_compute_state()`):
1. `ERROR` — if `load_warning` is set and scanner not loaded
2. `MEASURING_SET` / `MEASURING_SET_PAUSED` — from `nfs`
3. `MEASURING_SINGLE` — from `_single_measurement_running`
4. `TEST_SWEEP` — from `_test_sweep_running`
5. `HOMING` — from `scanner.get_state() == GrblMachineState.HOME`
6. `JOGGING` — from `_jog_in_progress`
7. `SINE_PLAYING` — from `_sine_playing`
8. `IDLE` — default

**Action conflict matrix** in `can_action()`:
| Action | Blocked by States |
|---|---|
| `jog` | HOMING, JOGGING, MEASURING_SET, MEASURING_SET_PAUSED, MEASURING_SINGLE, TEST_SWEEP |
| `home` | JOGGING, MEASURING_SET, MEASURING_SET_PAUSED, MEASURING_SINGLE, TEST_SWEEP |
| `measurement_set` | JOGGING, MEASURING_SINGLE, TEST_SWEEP, HOMING |
| `measurement_single` | JOGGING, MEASURING_SET, MEASURING_SET_PAUSED, TEST_SWEEP, HOMING |
| `test_sweep` | JOGGING, MEASURING_SET, MEASURING_SET_PAUSED, MEASURING_SINGLE, HOMING |
| `sine` | MEASURING_SET, MEASURING_SET_PAUSED, MEASURING_SINGLE, TEST_SWEEP |
| `settings` | MEASURING_SET, MEASURING_SET_PAUSED, MEASURING_SINGLE, TEST_SWEEP, HOMING |
| `project_browse` | MEASURING_SET, MEASURING_SET_PAUSED, MEASURING_SINGLE, TEST_SWEEP, HOMING |
| `grid_generate` | MEASURING_SET, MEASURING_SET_PAUSED, MEASURING_SINGLE, TEST_SWEEP |

#### 2. `control_pane.py` — Subscribe to state changes

- In `__init__` or constructor: connect `self.backend.state_changed` to a new `_on_state_changed(new_state: AppState)` handler.
- `_on_state_changed()` calls `_apply_state_to_gui(new_state)` which enables/disables jog buttons, control buttons, sine button, single measurement button, and test sweep button based on the new state.
- Remove state queries from `refresh_position()` — it only updates position text now.
- Remove state queries from `refresh_progress()` — it only updates progress bar now.
- In `_run_jog()`: wrap with `backend.set_jog_in_progress(True/False)` in a try/finally block.
- In `take_single_measurement()`: guard with `backend.can_action('measurement_single')` and wrap with `backend.set_single_measurement_running()`.
- In `test_sweep()`: guard with `backend.can_action('test_sweep')` and wrap with `backend.set_test_sweep_running()`.
- In `toggle_sine()`: wrap with `backend.set_sine_playing()`.
- In `_run_command('home')`: guard with `backend.can_action('home')`.

#### 3. `main_window.py` — Guard settings and project browse

- In `show_settings()`: check `backend.can_action('settings')`, show warning if blocked.
- In `browse_project()`: check `backend.can_action('project_browse')`, show warning if blocked.
- In `save_project_context()`: same guard.

#### 4. `grid_pane.py` — Guard grid generation

- In `generate_and_plan()`: check `backend.can_action('grid_generate')`, show warning if blocked.

### Architecture Diagram

``` mermaid
graph LR
    CP[ControlPane]
    MW[MainWindow]
    GP[GridPane]
    BM[BackendManager]
    NFS[NearFieldScanner]
    SCN[Scanner]
    
    BM --"state_changed signal"--> CP
    BM --"state_changed signal"--> MW
    BM --"state_changed signal"--> GP
    CP --"can_action(x)"--> BM
    MW --"can_action(x)"--> BM
    GP --"can_action(x)"--> BM
    BM --"_compute_state()"--> BM
    BM --"flags"--> BM
    BM --"is_measurement_set_running()"--> NFS
    BM --"get_state()"--> SCN
    
    BM --"jog()"--> SCN
    BM --"take_single_measurement()"--> NFS
    BM --"start_measurement_set()"--> NFS
    BM --"play_sine()"--> NFS
```

### Risks
- **Signal threading**: PySide6 signals emitted from worker threads may not reach GUI slots on the main thread. Mitigation: use `Qt.QueuedConnection` or ensure all flag setters are called from the main thread (they are, since jog/sine/home run in the GUI thread and measurement workers call back via existing signals).
- **State computation race**: `_compute_state()` reads multiple flags. If a flag changes mid-computation, the returned state might be stale for a few milliseconds. Mitigation: the state is used for GUI enable/disable, not safety-critical motion control.
- **Flag lifecycle**: If a worker thread crashes without clearing its flag, the state might be stuck. Mitigation: add `try/finally` blocks in all worker wrappers.
- **Backward compatibility**: Existing code that calls `is_measurement_set_running()` directly still works; the state machine is additive.

# Delivery Steps

### ✓ Step 1: Add AppState enum, signal, and state machine to BackendManager
Introduce `AppState` enum, PySide6 signal, and state tracking flags in `BackendManager`.
- Add `AppState` enum (IDLE, HOMING, JOGGING, MEASURING_SINGLE, MEASURING_SET, MEASURING_SET_PAUSED, TEST_SWEEP, SINE_PLAYING, ERROR) to `backend.py`.
- Add `state_changed: Signal(AppState)` PySide6 signal to `BackendManager`.
- Add private flags: `_jog_in_progress`, `_single_measurement_running`, `_test_sweep_running`, `_sine_playing`, `_last_state` to `BackendManager.__init__`.
- Implement `_compute_state()` that computes current state from flags + NFS + Scanner state with priority ordering.
- Implement `_emit_state_if_changed()` — compares computed state to `_last_state` and emits the signal if different (used by polling timers).
- Implement `_emit_state_now()` — always emits the current state (used by flag setters).
- Implement `can_action(action: str) -> bool` with the full action conflict matrix.
- Add setter/getter methods for each flag (`set_jog_in_progress`, `is_jogging`, etc.) — each setter calls `_emit_state_now()`.

### ✓ Step 2: Wire ControlPane to state machine via signal subscription
Update `ControlPane` to subscribe to `BackendManager.state_changed` and react to state transitions.
- Connect `self.backend.state_changed` to a new `_on_state_changed(new_state: AppState)` handler in `ControlPane.__init__`.
- `_on_state_changed()` calls `_apply_state_to_gui(new_state)` which enables/disables jog buttons, control buttons, sine button, single measurement button, and test sweep button based on the new state.
- `refresh_position()` calls `_emit_state_if_changed()` for homing/NFS state detection, then `_apply_state_to_gui()` to ensure GUI is current.
- `refresh_progress()` calls `_emit_state_if_changed()` for NFS measurement set state detection.
- Wrap `_run_jog()` with `backend.set_jog_in_progress(True/False)` in a try/finally block.
- Guard `take_single_measurement()` with `can_action('measurement_single')` and wrap with `set_single_measurement_running()`.
- Guard `test_sweep()` with `can_action('test_sweep')` and wrap with `set_test_sweep_running()`.
- Wrap `toggle_sine()` with `set_sine_playing()`.
- Guard `_run_command('home')` with `can_action('home')`.

### ✓ Step 3: Guard MainWindow and GridPane actions
Add state-based guards to settings, project browsing, and grid generation.
- In `main_window.py`, guard `show_settings()` with `backend.can_action('settings')` — show a warning dialog if blocked.
- In `main_window.py`, guard `browse_project()` with `backend.can_action('project_browse')` — show a warning if blocked.
- In `main_window.py`, guard `save_project_context()` with the same check.
- In `grid_pane.py`, guard `generate_and_plan()` with `backend.can_action('grid_generate')` — show a warning if blocked.
- Ensure all warning messages are consistent: 'Action blocked: <reason> is in progress.'

### ✓ Step 4: Add state indicator label to MainWindow header
Add a color-coded state badge to the top right of the header banner.
- In `main_window.py`, add styled `QLabel` to header showing current state name with colored background.
- Connect to `backend.state_changed` signal, update label text and color on each state transition.
- States: IDLE=green, HOMING=amber, JOGGING=blue, MEASURING=purple, PAUSED=amber, TEST SWEEP=pink, SINE=cyan, ERROR=red.

### ✓ Step 5: Fix state signal reliability — ensure flag setters always emit
Fix the race condition where polling timers and signal handlers both update `_last_state`, causing signal suppression.
- Split emission into `_emit_state_if_changed()` (deduplicating, for polling timers) and `_emit_state_now()` (unconditional, for flag setters).
- Flag setters use `_emit_state_now()` so the signal always fires when a Worker finishes.
- Worker signal connections use `Qt.QueuedConnection` so cleanup runs on the GUI thread.
- `refresh_position()` calls `_apply_state_to_gui()` after `_emit_state_if_changed()` to ensure GUI is current even if the signal hasn't been processed yet.