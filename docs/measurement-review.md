# Measurement Review

Open **Measurement Review** in the Views menu. This native PySide pane occupies
the workspace beneath the session header and follows the current session's
`measurement_set` directory. A completed measurement set opens the pane and scans
automatically, after acquisition has fully stopped. Stopped or failed full scans
do not open it automatically. Analysis runs in the
background and rejects results if the files change while
being read. New measurements mark existing results stale.

The table initially shows findings and incomplete checks. Switch to **All
recordings** to inspect normal measurements or **Excluded** to restore previous
exclusions. The recording list sits on the left, with Select,
Filename, Issue description, LF band and Chosen capture columns. Per-file status labels and LF
scores are omitted. Columns are fixed to their content widths, without wrapping
or stretching. The list initially takes only the width needed for those columns;
extra window width goes to the plots. There is no divider; the list remains
fitted to its contents while the plots expand. Buttons occupy a full-width row
above both panels.

Highlighting a recording shows three plots stacked in a single column on the
right: the raw microphone waveform, LF response and prediction, and measurement
locations. Purple shading marks the known sweep
interval(s); green/orange shading marks the actual pre/post noise windows. Details
below the list show noise levels, thresholds and window times.

Scroll over a plot to zoom around the pointer in both dimensions. Scroll over
an axis line, ticks or label to zoom only that axis. Log-frequency zoom respects
the logarithmic scale. Each plot has a **Reset axes** control at its top right,
which restores that plot's original limits.
Drag with the middle mouse button to pan a plot. Starting the drag over an axis
pans only that axis. The default Matplotlib toolbar is hidden.
Waveforms use a min/max overview when many samples are visible, switching to an
unfilled sample trace at close range. Detail is rebuilt once zooming/panning has
been idle for 150 ms; the existing trace is reused while the gesture continues.

## Checks

The spatial detector retains the standalone IR checker's 40–320 Hz complex-pressure
features and robust local affine predictor. It uses **12 neighbours only** and
requires 13 unique valid coordinates. Each of the three octave bands compares
prediction error with the larger of 10% of typical local pressure or eight times
the local deleted-residual scale, adjusted for prediction uncertainty. Any band
score above 1 marks a suspect. Scores are ratios, not probabilities. Real acoustic
structure, coordinate errors, gain changes and timing errors can also cause flags.
The common recording time reference is preserved; microphone arrivals are not
individually aligned away. Duplicate-coordinate attempts are reported separately
and omitted from the spatial fit. Both HALS filename conventions are supported.

Raw microphone checks detect silent/constant channels and at least three samples
at digital full scale (absolute amplitude >= 0.999). These produce suspect
findings. Environmental-noise checks use one RMS measurement per window:

* Pre: skip `max(20 ms, 20% of the recorded pre-marker interval)`; end 20 ms
  before the first marker. The marker is located in the electrical recording,
  so interface latency is accounted for.
* Post: start halfway between the final sweep end and recording end; stop 20 ms
  before recording end. For repeated sweeps, only the final tail is used.
* Require at least 50 ms usable audio for either window. Insufficient silence is
  reported as unassessed, rather than a pass. Window selection depends on timing,
  not finding a quiet part of an individual waveform.

New captures apply Post Silence once, without a second latency allowance.
Interface latency reduces the usable recorded tail. Increase Post Silence in
Audio Setup if a high-latency driver truncates the sweep or leaves insufficient
room decay or post-noise analysis time. Existing recordings keep their original
timing and are analysed using their saved metadata.

Noise levels must exceed both session and nearest-position baselines by the
greater of 12 dB or six robust standard deviations, and be above -45 dB relative
to microphone sweep RMS. Robust scale is 1.4826 times median absolute deviation
(with a 1 dB minimum scale). Local baselines use up to 12 other positions. At
least nine matching captures are required. Baseline groups share sample rate,
acquisition layout and sweep parameters. Multiple sweep repetitions are supported;
the sweep reference RMS combines all repetitions.

Pre-sweep environmental noise is the primary noise finding. Elevated late energy
is a review warning because it may be room decay. These checks detect outliers
within a set; consistently poor recordings may pass. There is no short-event
detector, electrical residual analysis, or full-IR prefix-energy finding here.
The full IR is not displayed in Measurement Review.

## Exclusions and reports

Nothing is automatically selected or excluded. Use the **Select** checkboxes to
choose recordings for actions. Shift-click filenames to highlight a range; clicking
one of that range's checkboxes checks or unchecks the whole highlighted range.
Space also toggles a highlighted range; Ctrl+A highlights all displayed rows.
The Select header is a plain label. Checked files outside the
current filter remain selected, and the action counts include them.

**Exclude (n)** stages an exclusion and greys the row. In the Excluded list, the
same button becomes **Restore (n)** and stages restoration. **Accept capture list** applies the proposals: excluded canonical
IRs receive `.blacklisted`, restored recordings lose the suffix. The pane then
rescans because neighbours have changed. Until acceptance, existing canonical
recordings remain active.

Each successful analysis automatically writes **one** `measurement_review.json`
in the measurement directory. Its readable findings section lists suspected or
incomplete files, reasons, and action statuses. The same file stores the complete
analysis results and compact complex LF features needed to reopen the pane and
its response plots. It does not copy raw waveforms. Exclusion and restoration
update this file immediately, and rescanning retains the reasons for excluded or
cleared findings. Ordinary passing recordings are omitted from the findings
section but retained in the analysis data for neighbour predictions and the All
recordings view. There are no separate text/CSV saves or export buttons.

Reopening a project restores its saved results automatically. A notice beneath
the buttons says **Saved measurement review results — re-scan to refresh**.
The notice is cleared after a successful rescan. If measurement files changed
since the saved scan, the historical results remain visible but file-changing
actions require a fresh scan. Raw waveform inspection reads any retained source
capture files. Missing, damaged or unsupported results show a rescan notice
rather than preventing the project from opening. Checkbox selections are reset
on reload.

## Re-capture and approval

Check recordings and press **Re-capture (n)** to measure those coordinates again.
The queue uses the normal motion manager and configured keep-out geometry, audio
owner thread, reference preparation and capture retries. It leaves the configured
grid and position log intact. Starting re-capture switches to Machine Control and
Live Capture. The coordinate readout shows the temporary re-capture queue, and its
progress counts only those selected positions. **Pause/Resume** and **Stop** remain
available in Machine Control (and in Review if opened during acquisition).
Successful attempts survive stopped/failed batches, and review scans again when
the batch ends. The app returns to Review, focuses the first re-captured filename
and displays its new attempt. Points whose original acquisition failed are listed as missing
and can also be re-captured or excluded.

The original capture is archived before the first re-capture. New captures go to
separate attempt folders under `review_attempts`, retaining their own raw capture,
full IR, settings and reference. Repeating adds capture 3, 4, etc. A row represents
one measurement position throughout.

Buttons above the plots select the attempt being inspected. Green means all
automatic checks passed, red means a check flagged it, and amber means checks or
capture data are incomplete. Viewing an attempt does not choose it. Press
**Choose this capture** to set the proposed winner; the Chosen capture column
shows the proposal. Viewing and choosing are both saved in `measurement_review.json`.
Rows with multiple complete captures show a blue circular badge with a white
number in that same column. The number is the count of captures available to choose from.
All attempts use the same active neighbour cohort, excluding every attempt at
their own position. Alternatives never become extra spatial neighbours.

**Accept capture list** publishes the proposed winners and exclusions. It warns
if any retained capture failed a check or has incomplete analysis. Incomplete
recordings without an IR cannot be chosen. Canonical files change only here;
all archived alternatives remain available. Publication uses staged file
replacement and a recovery journal inside the same JSON. Interrupted publication
rolls back when the dataset is reopened. Original sweep level, duration, repeat
count, channels, sample rate and protection settings must match when metadata is
available; pre/post silence and retry settings can be adjusted.

The JSON retains capture decisions and a dated action history alongside findings
and analysis. Rebuild downstream HALS imports/reconstruction after accepting a
replacement or exclusion; existing imports are not changed automatically.

## Analysis data retention

Saved measurements always retain raw captures, full IRs, references and diagnostic
metrics. The former debug-save user toggle has been removed; old configuration
values do not disable retention. Each successful capture also gets a
`debug/<stem>_capture.json` sidecar containing timing and its explicit reference
association. Legacy sets with a single reference archive remain supported;
ambiguous legacy associations are reported as unavailable.

After review, **Delete analysis data** estimates the space released and asks for
confirmation. It removes WAV/NPZ/JSON analysis artifacts under the selected
measurement set's `debug`, `Distortion` and `failed` directories, including those
inside capture attempts. It preserves every attempt's
linear IRs, `.blacklisted` linear IRs, project files and the automatic text report,
and updates that report first. It rejects linked/external paths
and changed deletion plans. Acquisition or analysis must finish before deletion.

Deletion prevents future raw-capture noise checks, full-IR inspection and capture
reprocessing. LF spatial review remains possible from the retained linear IRs.
Missing checks appear as incomplete when the set is scanned again.

## Performance and verification

Opening Measurement Review starts and warms a persistent pool in the background,
with `max(1, logical processors // 2)` worker processes (Windows' executor limit
is 61). Each worker is restricted to **one BLAS/OpenMP thread** using both startup
environment settings and runtime `threadpoolctl` limits. This avoids each process
starting another large numerical thread pool. Numerical kernels are warmed in
every worker before the pool is declared ready.

IR feature extraction, spatial fitting and raw microphone checks reuse this same
pool for every scan, including small sets. Work submission is bounded by the
worker count. Workers read their own WAVs; only paths, compact LF features and
findings cross process boundaries. Workers use spawn, never fork the Qt/audio
process. Cancellation stops new batches and leaves the pool ready for rescan.

Switching to another view or changing datasets retains the warm pool. Closing
the review widget or shutting down the app cancels analysis and releases the
pool, including when it is still warming. No pool starts at app launch while
review remains unopened.

The read-only comparison on `D:\HALS Measurments\D&D_8C_02` and its results are
documented in [the comparison report](../Documents/measurement_review_comparison/README.md).
Reproduce with:

```powershell
.venv\Scripts\python.exe tools\compare_measurement_review.py `
  'D:\HALS Measurments\D&D_8C_02' `
  'C:\Users\User\Documents\Git\HALS-Studio\tools\ir_capture_review' `
  'Documents\measurement_review_comparison'
```

`tools/benchmark_review_pool.py` warms the half-logical-processor pool and runs
two complete scans with the same workers, checks thread limits, and optionally
compares all statuses, scores and issue codes against an existing CSV report.
`tools/benchmark_review_spatial.py` remains available for isolated LF fitting.
Actual GUI timings depend on the machine and filesystem cache.
