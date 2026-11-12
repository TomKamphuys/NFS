---
sessionId: session-261111-171029-1jzr
---

# Requirements

### Overview & Goals
The first move of a measurement set (`move_to_safe_starting_radius`) currently moves the microphone diagonally (R and Z simultaneously) to `(retract_radius, z=0)`. If the mic starts at a small radius and positive Z (e.g., above the speaker after jogging), this diagonal path can cut through the no-fly zone — a potential collision risk with the $15,000 speaker under test.

**Goal:** Replace the diagonal move with a two-step sequence that first moves radially out to the safe radius, then moves vertically to z=0. This guarantees the path never enters the no-fly zone interior.

### Scope

**In Scope:**
- Modify `move_to_safe_starting_radius` in all three motion managers to use a two-step radial-then-vertical sequence.
- Update the existing tests that expect `planar_move_to` to be called.

**Out of Scope:**
- `move_to_park_position` — user confirmed to leave as-is.
- `move_to_park_position` safety (it uses the same pattern but happens after measurements when the mic is likely already at a safe position).
- Any changes to the `blocks_move` logic or no-fly zone geometry.

# Technical Design

### Current Implementation

All three motion managers (`CylindricalMeasurementMotionManager`, `FastCylindricalMeasurementMotionManager`, `SphericalMeasurementMotionManager`) in `src/nfs/motion_manager.py` implement `move_to_safe_starting_radius` identically:

1. Check if current radius ≥ retract_radius (skip if already outside).
2. Call `self._scanner.planar_move_to(retract_radius, 0.0)` — this moves R and Z simultaneously via a single `G0 X{z} Y{r}` command.

The `_perform_planar_move` method (used during measurement) already handles this correctly by moving radially out first, then vertically, then radially in.

### Proposed Changes

Replace the `planar_move_to(retract_radius, 0.0)` call with two sequential moves:
1. `self._scanner.radial_move_to(retract_radius)` — move out at current Z.
2. `self._scanner.vertical_move_to(0.0)` — move down to z=0 at the safe radius.

This matches the established safe pattern in `_perform_planar_move`.

**Files to modify:**
- `src/nfs/motion_manager.py` — three `move_to_safe_starting_radius` methods (lines ~396, ~626, ~816).
- `tests/test_cylindrical_measurement_motion_manager.py` — update test expectations.
- `tests/test_fast_cylindrical_measurement_motion_manager.py` — update test expectations if any.
- `tests/test_spherical_measurement_motion_manager.py` — update test expectations.

### Key Decisions
- **Always two-step:** Even when the direct path is safe, always split into two moves. This is the simplest and most conservative approach.
- **Park position unchanged:** User confirmed `move_to_park_position` does not need this fix.

# Testing

### Test Changes

**`tests/test_cylindrical_measurement_motion_manager.py`:**
- `test_move_to_safe_starting_radius`: Change assertion from `planar_move_to.assert_called_once_with(300.0, 0.0)` to verify `radial_move_to` called with `300.0` and `vertical_move_to` called with `0.0`.
- `test_move_to_safe_starting_radius_skipped_when_already_outside`: No change needed — the skip path is unchanged.

**`tests/test_spherical_measurement_motion_manager.py`:**
- `test_move_to_safe_starting_position`: Same change as above — expect `radial_move_to` + `vertical_move_to` instead of `planar_move_to`.
- `test_move_to_safe_starting_position_skipped_when_already_outside`: No change needed.

**`tests/test_fast_cylindrical_measurement_motion_manager.py`:**
- Check if any tests assert on the initial move behavior and update accordingly.

### Validation Approach
- Run `pytest tests/test_cylindrical_measurement_motion_manager.py tests/test_spherical_measurement_motion_manager.py tests/test_fast_cylindrical_measurement_motion_manager.py` to verify all tests pass.

# Delivery Steps

### * Step 1: Fix move_to_safe_starting_radius in all three motion managers
Replace the diagonal `planar_move_to(retract_radius, 0.0)` with two sequential moves: `radial_move_to(retract_radius)` followed by `vertical_move_to(0.0)` in all three motion managers.
- Modify `CylindricalMeasurementMotionManager.move_to_safe_starting_radius()` at line ~396 in `src/nfs/motion_manager.py`.
- Modify `FastCylindricalMeasurementMotionManager.move_to_safe_starting_radius()` at line ~626.
- Modify `SphericalMeasurementMotionManager.move_to_safe_starting_radius()` at line ~816.
- Update the docstrings to reflect the two-step movement.

###   Step 2: Update tests and validate
Update the test assertions that expect `planar_move_to` to be called, and verify all tests pass.
- Update `test_move_to_safe_starting_radius` in `tests/test_cylindrical_measurement_motion_manager.py` to expect `radial_move_to` + `vertical_move_to`.
- Update `test_move_to_safe_starting_position` in `tests/test_spherical_measurement_motion_manager.py` similarly.
- Check `tests/test_fast_cylindrical_measurement_motion_manager.py` for any affected tests.
- Run the relevant tests with `pytest` to confirm they pass.