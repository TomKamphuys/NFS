# Fast Cylindrical Motion Manager

`FastCylindricalMeasurementMotionManager` is a drop-in replacement for the
original `CylindricalMeasurementMotionManager`. It performs the cylindrical scan
much faster by moving all three axes (radial `R`, angular `θ`, vertical `Z`)
**simultaneously**, while keeping the exact same safety guarantee: the
microphone arm never travels through the volume enclosed by the measurement grid.

## Why the original manager is slow

The cylindrical point generator (`CylindricalMeasurementPoints`) scans the grid
as a zig-zag over three surfaces per angular sector: the **bottom cap**, the
**wall**, and the **top cap**. Almost every consecutive point changes its `Z`
coordinate.

The original manager treats *any* `Z` change conservatively:

1. move radially **out** to the no-fly zone's retract radius,
2. move `Z`,
3. move radially **in** to the target radius,

all as **three separate, sequential** moves. Because nearly every step changes
`Z`, virtually every measurement point pays for a full out-and-back detour of
three start/stop moves. This is safe but very time consuming — exactly the
prototype-era behaviour the new, stiffer setup no longer needs.

## What the fast manager does

The fast manager distinguishes two kinds of transitions:

### 1. Surface moves (the vast majority) — one simultaneous move

Consecutive points on the same surface (bottom cap, wall, top cap) are adjacent
zig-zag steps. They are executed as a **single** `G0 X.. Y.. Z..` command, so the
controller accelerates and decelerates **once** and interpolates all axes
together.

### 2. Interior-crossing transition — safe out-and-around

The only move that would cut through the grid is the jump from the end of the
top cap `(radius, θ_old, height)` to the start of the next sector's bottom cap
`(min_radius, θ_new, 0)`. The manager owns a `CylindricalNoFlyZone` and flags
exactly this move via `no_fly_zone.blocks_move(current, target)`. For it the
manager:

a. retracts radially to the no-fly zone's `retract_radius` (its wall radius,
   just outside the protected interior),
b. rotates to `θ_new` **and** travels to the target `Z` simultaneously, all
   while parked at the retract radius,
c. moves radially inward to the target radius.

## Why it is still safe (the invariant)

**Invariant:** the arm never enters the protected interior of the measurement
cylinder.

The proof rests on one property of the machine kinematics: each axis moves by
**linear interpolation**. Along a single move the radius is

```
r(s) = r_start + s · (r_end − r_start),   s ∈ [0, 1]
```

which is **monotonic** in `s`. Therefore the radius along any straight move never
drops below `min(r_start, r_end)`.

- **Surface moves.** Both endpoints lie on a measurement surface, so the whole
  segment stays on/against that surface:
  - *Caps*: `Z` stays inside the thin cap band near `Z = 0` or `Z = height`, i.e.
    it never crosses the mid-height bulk of the grid.
  - *Wall*: `r` stays `≥ radius − Δr` (the wall shell), i.e. it never moves inward
    into the bulk.
- **Interior-crossing transition.** The entire vertical sweep and rotation happen
  at the no-fly zone's retract radius (its wall radius), i.e. completely outside
  the protected interior. Only after reaching the target `Z` does the arm move
  radially inward, along the bottom cap plane.

Pure angular moves are rotations at constant `r`, so they never change how deep
the arm reaches; combined with the above, no motion ever enters the interior.

## How this is verified

See `tests/test_fast_cylindrical_measurement_motion_manager.py`:

- **Strategy unit tests** confirm surface moves issue exactly one combined
  command and the interior-crossing transition issues the retract → slew → move-in
  sequence.
- **Full-scan safety simulation** drives the *real* point generator through the
  manager with a `PathRecordingScanner` that reconstructs the straight-line path
  of every command and samples it densely (200 samples/segment). It asserts that
  **no** point on **any** segment enters the protected interior region
  `r < radius − Δr  AND  cap_spacing < z < height − cap_spacing`.
- A **negative control** proves the keep-out predicate has teeth: a naive direct
  shortcut across the middle *does* trip the assertion.
- An **order test** confirms the fast manager visits exactly the same points, in
  the same order, as the original.

## Time gain

The time model uses the same kinematics as the `[grbl_streamer]` mock in
`config.ini` (linear `300 mm/s`, angular `15 °/s`) plus a conservative
per-command overhead of `0.20 s` (comms round-trip + one accelerate/decelerate
ramp). Simultaneous axes are governed by the slowest axis; sequential commands
add up.

For a representative grid (8 angular × 3 radial-cap × 6 vertical points,
`radius = 300 mm`, `height = 400 mm`):

| Manager  | Commands | Modelled time |
|----------|----------|---------------|
| Original | 594      | ~6.4 min      |
| Fast     | 263      | ~2.5 min      |

**≈ 2.6× faster (~61 % time saved)** for the modelled grid. The gain grows with
the number of vertical/cap points, since those are where the original manager's
per-point detours dominate. The exact figures are printed by
`test_fast_manager_is_significantly_faster` (run pytest with `-s`).

## How to enable it

In `config.ini`, set the motion-manager type:

```ini
[motion_manager]
type = FastCylindricalMeasurementMotionManager
no_fly_radius = 300.0   # wall radius (mm) of the protected interior
no_fly_z_min = 0.0      # bottom cap plane (mm)
no_fly_z_max = 400.0    # top cap plane (mm)
```

The no-fly zone describes the keep-out volume around the device under test; the
arm retracts to `no_fly_radius` (the wall radius) for evasive moves. Everything
else (measurement-points configuration) is unchanged.
