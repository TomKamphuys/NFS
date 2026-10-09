# Balanced versus legacy grid results

Archived 9 October 2026. These are retained results from the completed comparison,
filtered to the two constructors retained in the app. Plots were redrawn from
saved data; no new simulations were run.

## Setup

R200 mm, full internal H500 mm, thickness50 mm, cutoff30 mm, azimuth -170 to
180 degrees, front weighting1. Legacy requested1000 and retained973 points;
balanced used the same973-point budget and seed42. Centre origin, pair swapping
off. Matrix metrics use grid_condition_util, orders5/8/12, 3–20kHz in100Hz steps.
Legacy uses P_side0.5/P_caps0.8 and dual spirals. See settings.json for parameters.

## Matrix and geometry results

- [Point placement and wall density](positions.png)
- [N=5](metrics_N5.png), [N=8](metrics_N8.png), [N=12](metrics_N12.png)
- metrics.csv, summary.csv and geometry.csv retain numerical results.
- balanced.csv and legacy.csv preserve the actual grids.

At N=8, worst condition improved from17.40 to15.03dB and worst field overlap
from0.9373 to0.9047. Improvements are not universal across all metrics/frequencies.
Higher EDOF is generally preferable; lower is preferable for the other plotted
matrix metrics. Matrix overlap is not a solved I/E ratio.

## Directional-source solve

[30mm source model, solver checks and results](piston30/README.md).
Plots and raw trial powers are retained in piston30.

## Scope

This is one geometry and one balanced seed, not a claim of universal superiority.
The later pair-wise optimiser was disabled for all retained comparisons.
Temporary experiment scripts and the unused uniform-angular variant were removed.
