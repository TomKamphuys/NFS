# Safety Analysis: No-Fly Zone Protection

This document describes the safety measures implemented to prevent the microphone arm from colliding with the device under test (DUT), as well as the known gaps and failure modes that are not yet addressed by the software.

## Implemented Safety Measures

The system employs multiple layers of protection, from offline simulation to runtime checks.

### 1. No-Fly Zone Geometry

The core safety concept is a **keep-out volume** around the DUT that the microphone arm must never enter. Two zone geometries are supported:

- **CylindricalNoFlyZone**: A cylinder defined by wall radius `r_wall`, bottom cap `z_min`, and top cap `z_max`. A point is inside the zone when `r < r_wall - tolerance AND z_min + tolerance < z < z_max - tolerance`. A straight move between two points is flagged as potentially crossing the zone when the endpoints are not both below the bottom cap, both above the top cap, or both outside the wall radius.

- **SphericalNoFlyZone**: A sphere of radius `radius` centered on the origin. A point is inside when its length `sqrt(r² + z²) < radius - tolerance`. A move is flagged when either endpoint lies inside the sphere.

Both zones enforce a small tolerance margin (0.1 mm) so that points resting exactly on the boundary are treated as safe.

### 2. Offline Motion Simulation

Before a measurement set starts, the system can run a **full offline simulation** of the planned motion (`motion_simulation.py`). The simulation:

- Builds the real motion manager with the real point generator and no-fly zone.
- Drives a `RecordingScanner` (a hardware-free mock) through all measurement points.
- Records every motion segment at high resolution: **1 mm** sampling for straight moves, **1°** sampling for arc moves.
- Checks all **measurement endpoints** against the no-fly zone via `zone.contains()`.
- Checks all **sampled path positions** (intermediate points between measurement endpoints) against the no-fly zone.

The simulation results are presented in the **Motion Preview** GUI view, which draws the full planned motion in the r/z plane overlaid with the no-fly zone. Unsafe points and paths are flagged with a warning banner and pop-up dialog.

### 3. Pre-Measurement Check

When the user starts a measurement set, the system runs the same motion simulation one more time (`control_pane.py`). If unsafe points or paths are detected, a dialog appears offering the user a choice to **Cancel** or **Continue** anyway. This is the last gate before any physical motion is issued.

### 4. Runtime Endpoint Check

Every motion manager checks each target measurement point against the no-fly zone **before** issuing any motion command (`_acquire_next_safe_position()` in `motion_manager.py`). If a point lies inside the zone, the configured `unsafe_point_policy` decides what happens:

- **`abort`** (default, safest): the scan stops immediately with an `UnsafeMeasurementPointError` — the arm never moves toward the unsafe point.
- **`skip`**: the offending point is logged and skipped, and the scan continues with the next point.

### 5. Runtime Path Check (Evasive Maneuvers)

The motion managers use different strategies to ensure the **path** between two safe endpoints does not cross the no-fly zone:

- **CylindricalMeasurementMotionManager**: Uses a conservative strategy by design. Vertical moves always retract to the safe radius first, so the path never crosses the interior. No explicit `blocks_move()` check is needed — the movement sequence itself guarantees safety.

- **FastCylindricalMeasurementMotionManager**: Explicitly calls `zone.blocks_move(current, target)` before each move. If the direct path would cross the zone, it performs a three-step evasive maneuver: retract to safe radius → slew to target angle and height → move radially inward.

- **SphericalMeasurementMotionManager**: Uses arc-based movement that stays on or outside the sphere by design. Calls `blocks_move()` to detect when an evasive retract is needed.

### 6. Safe Starting Position

Before any measurement motion, the manager moves the arm to a **safe starting radius** (just outside the no-fly zone) at zero height. If the arm is already outside the zone, this move is skipped.

### 7. Park Position

After a measurement set completes, the arm is moved to a defined **park position**: theta = 0, z = 0, radius = retract radius (just outside the no-fly zone).

### 8. GRBL Error Handling

The GRBL controller (`ESP32Duino` in `grbl_controller.py`) waits for an `ok` acknowledgment after each G-code command. If the controller reports an `error` response (e.g., soft limit violation), a `RuntimeError` is raised, which propagates up through the motion manager and scanner to the measurement orchestrator, causing the scan to fail with a logged error.

### 9. GRBL Alarm State

If the hardware hits a hard limit switch, GRBL enters `ALARM` state and stops all motion. The software detects this state transition and clears the `_homed` flag, requiring explicit recovery (soft reset + kill alarm + re-home) before further motion.

## Summary of Protection Layers

| Layer | When | What it checks | Where |
|---|---|---|---|
| Motion simulation | Before scan starts | Endpoints + sampled paths | `motion_simulation.py` |
| Pre-measurement dialog | Before scan starts | Endpoints + sampled paths | `control_pane.py` |
| Runtime endpoint check | Before each move | Target point | `motion_manager.py` |
| Runtime path check | Before each move | Path between endpoints | `motion_manager.py` |
| GRBL soft limits | During motion | Axis travel limits | GRBL firmware |
| GRBL hard limits | During motion | Physical endstops | GRBL firmware |

---

## Known Gaps — "Gaten in de Wet" (Holes in the Law)

Despite the multiple layers of protection, there are failure modes that the current system does not address. These are listed in order of severity.

### 1. Stepper Motor Step Skipping

**The most critical gap.** The system has **no real-time position feedback** during motion.

The GRBL controller tracks position by counting stepper pulses sent to the motors. It does not read encoders or any other feedback device. If a stepper motor skips steps — due to excessive acceleration, mechanical binding, belt slip, or power fluctuation — the controller's internal position counter diverges from the actual physical position.

**Consequences:**
- The software believes the arm is at position A, but it's actually at position B.
- All subsequent moves are calculated from the wrong position.
- The no-fly zone checks use the wrong position, so they may authorize a move that actually crosses into the DUT.
- The error compounds with each move — a small step skip early in the scan can lead to a large offset by the end.

**When step skipping is most likely:**
- High acceleration rates (G0 rapid moves).
- Heavy mechanical load or friction.
- Belt tension issues or worn belts.
- Insufficient motor current (driver configuration).
- Resonance at certain speeds.

**Mitigation (not implemented):**
- Configure GRBL with conservative acceleration and velocity limits.
- Use closed-loop stepper drivers with feedback.
- Add optical encoders to one or more axes.
- Periodically re-home during long scans.

### 2. No Mid-Motion Abort

Once a G-code command is sent to GRBL, the software **cannot stop the motion** until the command completes.

The `hold()` method exists but is not called automatically. If the no-fly zone checks have a bug and authorize an unsafe path, the arm will physically execute the entire move before the software can react. The only thing that can stop it mid-motion is:

- **GRBL soft limits** (if configured in the firmware) — but these are axis-level limits, not no-fly zone geometry.
- **GRBL hard limits** (physical endstops) — but these are typically at the travel extremes, not around the DUT.

**Mitigation (not implemented):**
- Implement a watchdog that monitors the GRBL state report stream during motion and calls `hold()` if the reported position enters the no-fly zone.
- Configure GRBL soft limits to match the no-fly zone boundaries (coarse approximation only).

### 3. Serial Communication Failure

The software communicates with GRBL over a serial (USB/UART) connection. If the connection drops or becomes corrupted:

- Commands may be lost without acknowledgment.
- The `_wait_for_ack()` method will eventually time out, raising a `TimeoutError`.
- However, the timeout is only checked after a command is sent — if the connection drops during motion, the software won't know until the next command.
- Position reports from GRBL stop arriving, so the software's view of the position freezes at the last known value.

**Mitigation (not implemented):**
- Implement a heartbeat or periodic position-ping during motion.
- Monitor the serial connection health independently.

### 4. Power Loss

If power to the ESP32/GRBL controller or stepper drivers is interrupted:

- The controller loses all state, including position.
- Stepper motors lose their position (no holding torque without power).
- The arm may drop or drift under gravity.
- On restart, the software has no way to know the actual arm position.

**Mitigation (partially implemented):**
- The system requires homing after power-up (the `_homed` flag is cleared on alarm).
- A battery-backed real-time clock or absolute encoders would help, but are not implemented.

### 5. Position Stale After Alarm Recovery

When GRBL enters ALARM state (e.g., hard limit hit) and the user clears the alarm with `$X`, the position is reset. The software tracks this via the `_homed` flag, but there is a window where:

- The alarm is cleared but the user hasn't re-homed.
- The software might attempt motion with an unknown position.

**Mitigation (partially implemented):**
- The `is_homed()` check can be used before starting motion, but is not enforced at all entry points.

### 6. No-Fly Zone Configuration Error

If the no-fly zone parameters are entered incorrectly in the configuration:

- The zone might be too small, leaving parts of the DUT unprotected.
- The zone might be too large, causing false-positive skips.
- The zone geometry might not match the actual DUT shape (e.g., a cylindrical zone around a non-cylindrical DUT).

**Mitigation (partially implemented):**
- The Motion Preview shows the zone overlaid on the planned path, allowing visual verification.
- Parameter validation rejects non-finite or non-positive values.
- The tolerance margin (0.1 mm) provides a small safety buffer.

### 7. Simulation vs. Reality Mismatch

The motion simulation uses a mock scanner that assumes perfect execution of every command. In reality:

- Moves take time, and the arm's actual trajectory may differ from the ideal linear interpolation.
- Mechanical play, backlash, or flexure can cause the arm to deviate from the commanded path.
- The simulation samples at 1 mm / 1°, but the actual path between samples could still deviate due to mechanical imperfections.

**Mitigation (not implemented):**
- Add a larger safety margin to the no-fly zone for the simulation than for the runtime checks.
- Characterize the mechanical accuracy of the rig and incorporate it into the safety model.

### 8. Concurrent Access

If the user manually moves the arm (via the GUI controls) while a measurement set is running, or if two threads issue commands simultaneously:

- The position tracking could become inconsistent.
- A manual move could place the arm inside the no-fly zone.

**Mitigation (partially implemented):**
- The GUI disables controls during measurement.
- The GRBL controller processes commands sequentially (one at a time).

### 9. Floating-Point Precision

The no-fly zone checks use floating-point comparisons. While the tolerance margin (0.1 mm) provides some buffer, edge cases near the boundary could theoretically be misclassified due to floating-point rounding.

**Mitigation (partially implemented):**
- The tolerance parameter in `contains()` provides a small margin.
- The `blocks_move()` check uses `<=` and `>=` comparisons, which are conservative.

---

## Recommendations

### High Priority

1. **Address stepper step skipping**: This is the single most dangerous gap. Consider:
   - Adding a note in the user documentation about configuring conservative acceleration/velocity.
   - Exploring closed-loop stepper drivers for the rig.
   - Implementing a periodic position verification step (e.g., re-home every N points).

2. **Implement mid-motion monitoring**: Add a watchdog thread that reads the GRBL state report stream during motion and calls `hold()` if the reported position enters the no-fly zone. This would catch both step-skipping and software bugs.

### Medium Priority

3. **Serial connection health monitoring**: Add a heartbeat mechanism to detect dropped connections before they cause position drift.

4. **Enforce homing check**: Require `is_homed()` to be true before any motion command is issued, not just at scan start.

5. **Document GRBL configuration**: Provide recommended GRBL settings (acceleration, velocity, soft limits) in the user documentation.

### Low Priority

6. **Larger simulation safety margin**: Add a configurable margin to the no-fly zone used in simulation, separate from the runtime zone, to account for mechanical imperfections.

7. **Mechanical characterization**: Measure the actual accuracy and repeatability of the rig, and incorporate the results into the safety model.

---

## Conclusion

The current safety architecture provides strong protection against **software-level** collisions: the no-fly zone is checked at multiple stages, from offline simulation to runtime, and the motion managers use proven strategies to avoid the protected interior. The redundancy is appropriate for a safety-critical system.

However, the system has **no protection against hardware-level failures**, particularly stepper step skipping. Once a G-code command leaves the software, there is no way to verify or abort the physical motion until it completes. The system relies entirely on the assumption that the hardware executes every command exactly as commanded — an assumption that can fail in practice.

The most impactful improvement would be to add real-time position monitoring during motion, either through GRBL state reports or through external encoders. This would close the gap between the software's belief about position and the arm's actual position.
