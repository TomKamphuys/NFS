# Optional measurement ordering

Enable `optimize_point_order = True` in `[motion_manager]`, or use the matching
checkbox in Motion Manager settings. It defaults to False (original order).
All three built-in motion managers support it.

The objective is weighted travel, not physical microphone distance or predicted
time. Azimuth rotation costs **300 mm-equivalent per 90 degrees** (3.333 mm per
degree), independent of radius, so small-radius rotations are no longer cheap.
This penalty is added to linear R/Z travel, including simultaneous moves.
-180 to +180 costs 1200 mm-equivalent: a full 360-degree movement through zero,
never a wraparound shortcut. Cylindrical retractions and spherical R/Z arcs are
included. Execution still uses the existing safe motion manager.

The deterministic nearest-neighbour heuristic retains the original first point
(and therefore the original approach move). Its candidate is used only if its
weighted inter-point cost is lower than the original route. This is not
a guarantee of the global shortest route or minimum elapsed time. Computation
is quadratic in point count and occurs when the motion manager is created.

Every input entry, including duplicates, remains a separate measurement. Unsafe,
non-finite, negative-radius, or out-of-range angular points reject optimization
before motion; they are not discarded, even with the legacy skip policy. Fix the
grid rather than relying on optimization to omit inaccessible measurements.
The optimizer preserves the points supplied by the point plugin; any filtering
configured in that plugin happens before ordering.