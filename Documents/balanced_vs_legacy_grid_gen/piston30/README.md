# 30 mm forward-directed source: actual separation solves

## Purpose

Compare the balanced and legacy grids using an internal-only directional field, not
worst-case matrix overlap. Pair swapping is off. No production settings changed.

## Source model

A uniform circular disk of radius 15 mm centred at the cylinder centre in the YZ
plane, facing +X (phi=0, z=0). Each disk element contains a monopole and a co-located
X-directed dipole. With exp(+i omega t) convention, an element contributes

    0.5 * exp(-ikd)/d * [1 + cos(gamma)*(1 + 1/(ikd))]

where d is source-element-to-observer distance and gamma is its angle from +X.
Disk weights integrate to one. The entire source lies inside the measurement
surface and generates an outgoing Helmholtz solution; true external field is zero.
No artificial rear-hemisphere pressure cutoff is applied.

Far-field pattern is the circular-piston factor multiplied by a cardioid envelope:

    [2 J1(ka sin(theta))/(ka sin(theta))] * (1+cos(theta))/2.

The piston factor provides the 30 mm frequency-dependent narrowing. The cardioid
is an explicit modelling choice to produce forward radiation on the whole sphere.
This is not an exact infinitely baffled piston, real cabinet, dome breakup, or
baffle-diffraction simulation. Low-frequency forward bias is also imposed by
this model. See directivity.png for the pattern actually tested.

Piston reference: Daniel A. Russell, Penn State,
https://www.acs.psu.edu/drussell/demos/baffledpiston/baffledpiston.html

Disk integration uses 12-point Gauss-Legendre quadrature in squared radius and
64 uniform azimuth samples. Doubling both to 24/128 at 3,17,20 kHz on a subset
of observers changed pressures by less than 1.5e-15 in relative L2 norm.

## Solver and measurements

- HALS-Studio/process_engine/she_solver_core.py build_she_matrix is imported.
- Its outgoing h_n^(2) columns are internal; regular j_n columns are external,
  interleaved in that order for each (n,m).
- Unregularised SVD inversion, mathematically identical to max_lambda=0.
  Batched right-hand sides reuse the decomposition for noise trials.
- Checked coefficients against the actual _solve_one_frequency call at each
  method/order combination at 3,17,20 kHz: all retained checks passed rtol=1e-9,
  atol=1e-11. No order dropping, row weighting, or adaptive damping.
- Orders N=5,8,12; 3000–20000 Hz in 250 Hz steps, 69 frequencies.
- Same 973-point grids as the geometric comparison, R200/H500, 50 mm thickness,
  phi -170..180, azimuth density ratio 1, centre origin. No coordinate offsets.
- Evaluation uses 1600 independent approximately equal-area Fibonacci directions
  on a 450 mm-radius sphere outside every measurement point.
- Fixed per-frequency on-axis pressure magnitude at 200 mm normalized to 1.
- Noise-free, and spatially independent circular complex Gaussian measurement
  noise of RMS 0.001 or 0.01: -60 or -40 dB relative to that on-axis reference.
  These are illustrative floors, not claims about the hardware's actual SNR.
- Eight noise realisations per level. The same random arrays are used across
  grids for paired comparisons; rows need not be the same physical positions.

## Metrics

Powers are mean squared complex pressure on the common observation sphere.
I/E = 10 log10(reconstructed internal power / reconstructed external power).
Internal error = 10 log10(mean |reconstructed internal - true field|^2 /
                         mean |true field|^2).
Higher I/E and more negative error are better. metrics.csv also provides leakage
referenced to TRUE internal power, so a distorted numerator need not hide leakage.
Powers are averaged across noise trials before converting to dB. HF summary is
an arithmetic average of those per-frequency dB values from 17 through 20 kHz.
These are pressure-energy ratios on a specified sphere, not acoustic power flux
or a raw coefficient-norm ratio; their values depend on observation radius.

## Findings and files

At N=8 over 17–20 kHz with the -60 dB noise floor, mean I/E was 48.75 dB
for legacy and 49.35 dB for balanced. Internal error was -47.31 and -47.64 dB,
respectively. These are modest improvements in this particular model.

See solve_N5.png, solve_N8.png and solve_N12.png. directivity.png shows the
assumed source. metrics.csv, trials.csv, hf_summary.csv and checks.json retain
the numerical evidence. Data were filtered from the completed experiment;
solves were not rerun. The temporary benchmark scripts have been removed.

Noise-free ratios above 100 dB describe ideal numerical behaviour, not hardware
performance. Noise and harmonic order mattered more than the small grid differences.
