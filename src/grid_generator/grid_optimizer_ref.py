"""Deterministic depth exchanges between grid generation and path planning.

Dependencies: NumPy, pandas, threadpoolctl and SciPy with sph_harm_y and batched solve_triangular.
See README_grid_optimizer.md for the CLI and import API.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import time
import os
import multiprocessing as mp
import traceback
# CLI performance defaults; importing the helper does not alter host thread settings.
if __name__ == '__main__':
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
    os.environ.setdefault('OMP_NUM_THREADS', '1')
import numpy as np
import pandas as pd
from scipy.linalg import solve_triangular
from scipy.special import sph_harm_y, spherical_jn, spherical_yn

COLUMNS = ["r_xy_mm", "phi_deg", "z_mm"]

def xyz(coordinates):
    r, phi, z = coordinates.T
    angle = np.deg2rad(phi)
    return np.column_stack([r*np.cos(angle), r*np.sin(angle), z])/1000


class MatrixSet:
    def __init__(self, coordinates, order, origins, frequencies, sound_speed):
        self.origins = origins
        self.frequencies = frequencies
        self.sound_speed = sound_speed
        self.order = order
        self.degree = np.repeat(np.arange(order+1), 2*np.arange(order+1)+1)
        self.m = np.concatenate([np.arange(-n, n+1) for n in range(order+1)])
        self.q = len(self.degree)
        a = self.rows(coordinates)
        self.gram = a.conj().transpose(0, 2, 1) @ a
        self.baseline = self.metrics(self.gram)

    def rows(self, coordinates):
        result = []
        for origin in self.origins:
            points = xyz(coordinates)-origin
            r = np.linalg.norm(points, axis=1)
            if np.any(r <= 0):
                raise ValueError('An expansion origin coincides with a microphone')
            theta = np.arccos(np.clip(points[:, 2]/r, -1., 1.))
            phi = np.arctan2(points[:, 1], points[:, 0])
            y = sph_harm_y(self.degree[None, :], self.m[None, :], theta[:, None], phi[:, None])
            kr = 2*np.pi*self.frequencies[:, None, None]/self.sound_speed*r[None, :, None]
            n = np.arange(self.order+1)[None, None, :]
            j = spherical_jn(n, kr)[:, :, self.degree]
            h = j + 1j*spherical_yn(n, kr)[:, :, self.degree]
            result.append(np.concatenate([j*y, h*y], axis=2))
        return np.concatenate(result)

    def proposal(self, old, new):
        rows = self.rows(np.concatenate([old, new]))
        old_rows, new_rows = rows[:, :2], rows[:, 2:]
        return (self.gram + new_rows.conj().transpose(0, 2, 1) @ new_rows
                - old_rows.conj().transpose(0, 2, 1) @ old_rows)

    def metrics(self, gram):
        eigenvalues = np.linalg.eigvalsh(gram)
        if np.any(eigenvalues[:, 0] <= 0):
            raise np.linalg.LinAlgError('Nonpositive Gram eigenvalue')
        condition = 10*np.log10(eigenvalues[:, -1]/eigenvalues[:, 0])
        q = self.q
        left = np.linalg.cholesky(gram[:, :q, :q])
        right = np.linalg.cholesky(gram[:, q:, q:])
        overlap = solve_triangular(left, gram[:, :q, q:], lower=True, check_finite=False)
        overlap = solve_triangular(right, overlap.conj().transpose(0, 2, 1),
                                   lower=True, check_finite=False).conj().transpose(0, 2, 1)
        rho = np.minimum(np.linalg.svd(overlap, compute_uv=False)[:, 0], 1.)
        diag = np.real(np.diagonal(gram, axis1=1, axis2=2))
        coherence = np.abs(gram)/np.sqrt(diag[:, :, None]*diag[:, None, :])
        idx = np.arange(2*q)
        coherence[:, idx, idx] = 0.
        peak = coherence.max(axis=(1, 2))
        mean_squared = (coherence**2).sum(axis=(1, 2))/(2*q*(2*q-1))
        return np.column_stack([condition, rho, peak, mean_squared])

    def condition_guard(self, metrics):
        # Guard both the average and the worst raw condition within each origin.
        candidate = metrics[:, 0].reshape(len(self.origins), -1)
        baseline = self.baseline[:, 0].reshape(len(self.origins), -1)
        return (np.all(candidate.max(axis=1) <= baseline.max(axis=1)+1e-8)
                and np.all(candidate.mean(axis=1) <= baseline.mean(axis=1)+1e-8))

    def rebase(self, coordinates):
        a = self.rows(coordinates)
        exact = a.conj().transpose(0, 2, 1) @ a
        np.testing.assert_allclose(self.gram, exact, rtol=1e-9, atol=1e-11)
        self.gram = exact


def score(metrics_list, origin_count):
    costs = []
    for metrics in metrics_list:
        # Separation noise penalty plus column-normalised coherence terms.
        separation_db = -10*np.log10(np.maximum(1-metrics[:, 1]**2, 1e-15))
        costs.append(separation_db.mean() + .35*separation_db.reshape(origin_count, -1).max(axis=1).mean()
                     + 2*metrics[:, 2].mean() + 10*metrics[:, 3].mean())
    return float(np.mean(costs))



def _matrix_worker(pipe, coordinates, specifications, sound_speed):
    """Own matrices throughout the search; exchange only coordinates and metrics."""
    try:
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=1):
            matrices = [MatrixSet(coordinates, n, np.array([origin]), frequencies, sound_speed)
                        for n, origin, frequencies in specifications]
            pipe.send(('ok', [m.baseline for m in matrices]))
            pending = None
            while True:
                command, payload = pipe.recv()
                if command == 'stop':
                    return
                if command == 'accept':
                    for matrix, gram in zip(matrices, pending):
                        matrix.gram = gram
                    continue
                if command == 'evaluate':
                    pending = [m.proposal(*payload) for m in matrices]
                    try:
                        result = [m.metrics(g) for m, g in zip(matrices, pending)]
                    except np.linalg.LinAlgError:
                        result = None  # Numerically singular candidate: reject it.
                elif command == 'rebase':
                    for matrix in matrices:
                        matrix.rebase(payload)
                    result = [m.metrics(m.gram) for m in matrices]
                else:
                    raise ValueError(f'Unknown worker command: {command}')
                pipe.send(('ok', result))
    except BaseException:
        try:
            pipe.send(('error', traceback.format_exc()))
        except (OSError, EOFError):
            pass
    finally:
        pipe.close()


class _ParallelEvaluator:
    def __init__(self, coordinates, orders, origins, frequencies, sound_speed, workers):
        self.pipes, self.processes, self.layouts = [], [], []
        self.shape = (len(orders), len(origins)*len(frequencies), 4)
        self.origin_count = len(origins)
        ctx = mp.get_context('spawn')
        # Contiguous slices preserve the serial order/origin/frequency reductions.
        cells = [(n, o, f) for n in range(len(orders))
                 for o in range(len(origins)) for f in range(len(frequencies))]
        try:
            for indices in np.array_split(np.arange(len(cells)), min(workers, len(cells))):
                specifications, layout = [], []
                for n in range(len(orders)):
                    for o in range(len(origins)):
                        fs = [cells[int(i)][2] for i in indices
                              if cells[int(i)][:2] == (n, o)]
                        if fs:
                            specifications.append((orders[n], origins[o], frequencies[fs]))
                            layout.append((n, o*len(frequencies)+np.asarray(fs)))
                parent, child = ctx.Pipe()
                process = ctx.Process(target=_matrix_worker,
                    args=(child, coordinates, specifications, sound_speed))
                try:
                    process.start()
                except BaseException:
                    parent.close()
                    raise
                finally:
                    child.close()
                self.pipes.append(parent)
                self.processes.append(process)
                self.layouts.append(layout)
            self.baseline = self.receive()
        except BaseException:
            self.close()
            raise

    def send(self, command, payload=None):
        for pipe in self.pipes:
            pipe.send((command, payload))

    def receive(self):
        result = np.empty(self.shape)
        feasible = True
        for pipe, process, layout in zip(self.pipes, self.processes, self.layouts):
            while not pipe.poll(0.2):
                if not process.is_alive():
                    raise RuntimeError(f'Optimiser worker exited with code {process.exitcode}')
            try:
                status, values = pipe.recv()
            except EOFError as exc:
                raise RuntimeError('Optimiser worker closed unexpectedly') from exc
            if status != 'ok':
                raise RuntimeError(values)
            if values is None:
                feasible = False
            else:
                for (n, rows), value in zip(layout, values):
                    result[n, rows] = value
        return list(result) if feasible else None

    def guard(self, metrics):
        if metrics is None:
            return False
        for baseline, candidate in zip(self.baseline, metrics):
            a = baseline[:, 0].reshape(self.origin_count, -1)
            b = candidate[:, 0].reshape(self.origin_count, -1)
            if not (np.all(b.max(axis=1) <= a.max(axis=1)+1e-8)
                    and np.all(b.mean(axis=1) <= a.mean(axis=1)+1e-8)):
                return False
        return True

    def close(self):
        for pipe in self.pipes:
            try:
                pipe.send(('stop', None))
            except (OSError, EOFError):
                pass
            pipe.close()
        for process in self.processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join()

def optimize_grid(grid, *, radius_mm=None, center_z_mm=None,
                  orders=(5, 8), frequencies_hz=None,
                  sound_speed=343., proposals=1000, seed=20261001,
                  progress=print):
    """Return (new_dataframe, report); never mutate the input.

    Evaluation uses only the physical cylinder centre as the expansion origin.
    Radius and centre are read from grid_gen metadata unless explicitly supplied.
    Exchanges use positional row indices; dataframe index and all other columns
    are preserved. Progress is a callable accepting a string, or None.
    """
    from threadpoolctl import threadpool_limits  # One BLAS thread per process.
    workers = max(1, (os.cpu_count() or 1)//2)
    started = time.perf_counter()
    df = pd.read_csv(grid) if isinstance(grid, (str, Path)) else pd.DataFrame(grid).copy(deep=True)
    if not set(COLUMNS).issubset(df.columns):
        raise ValueError(f"Grid requires columns {COLUMNS}")
    metadata = {}
    for value in df.get('gen_settings', pd.Series(dtype=str)).dropna():
        key, sep, val = str(value).partition('=')
        if sep:
            metadata[key] = val
    if radius_mm is None:
        if 'cyl_radius_internal' not in metadata:
            raise ValueError('Supply radius_mm when generator metadata is absent')
        radius_mm = 1000 * float(metadata['cyl_radius_internal'])
    if center_z_mm is None:
        offset = metadata.get('z_offset_mm', 'None')
        if offset != 'None':
            center_z_mm = float(offset)
        elif metadata.get('z_midpoint_zero') == 'True':
            center_z_mm = 0.
        elif metadata.get('z_midpoint_zero') == 'False' and 'cyl_height_external' in metadata:
            center_z_mm = 500 * float(metadata['cyl_height_external'])
        else:
            raise ValueError('Supply center_z_mm when generator centre metadata is absent')
    frequencies = np.asarray(np.arange(3250., 20000., 1500.) if frequencies_hz is None else frequencies_hz, dtype=float)
    origins = np.zeros((1, 3))  # Fixed cylinder-centred expansion.
    if frequencies.ndim != 1 or not len(frequencies) or not np.isfinite(frequencies).all() or np.any(frequencies <= 0):
        raise ValueError('frequencies_hz must be finite and positive')
    orders = tuple(orders)
    if not orders or any(not isinstance(n, (int, np.integer)) or n < 0 for n in orders):
        raise ValueError('orders must contain nonnegative integers')
    if not isinstance(proposals, (int, np.integer)) or proposals < 0:
        raise ValueError('proposals must be a nonnegative integer')
    if not np.isfinite([radius_mm, center_z_mm, sound_speed]).all() or radius_mm <= 0 or sound_speed <= 0:
        raise ValueError('Geometry and sound speed must be finite, radius and sound speed positive')
    original = df[COLUMNS].to_numpy(dtype=float)
    original[:, 2] -= center_z_mm
    if not np.isfinite(original).all() or np.any(original[:, 0] < 0):
        raise ValueError('Coordinates must be finite with nonnegative cylindrical radii')
    if len(np.unique(original, axis=0)) != len(original):
        raise ValueError('Input contains duplicate coordinates')
    if len(original) < 2*(max(orders)+1)**2:
        raise ValueError('Too few points for requested internal/external harmonic orders; lower orders')
    current = original.copy()
    groups = [(np.flatnonzero(current[:, 0] >= radius_mm), 0),
              (np.flatnonzero((current[:, 0] < radius_mm) & (current[:, 2] > 0)), 2),
              (np.flatnonzero((current[:, 0] < radius_mm) & (current[:, 2] < 0)), 2)]
    if sum(len(g) for g, _ in groups) != len(current):
        raise ValueError('Points inside the cylinder midplane cannot be classified as wall/caps')
    groups = [(g, c) for g, c in groups if len(g) >= 2]
    if not groups:
        raise ValueError('No surface has enough points for exchanges')
    def emit(message):
        if progress is not None:
            progress(message)
    emit(f'Initialising {len(current)} points, orders {orders}, {len(origins)} origins, {len(frequencies)} frequencies; {min(workers, len(orders)*len(origins)*len(frequencies))} workers')
    evaluator = _ParallelEvaluator(current, orders, origins/1000, frequencies, sound_speed, workers)
    try:
        initial = best = score(evaluator.baseline, len(origins))
        rng = np.random.Generator(np.random.PCG64(seed))
        probabilities = np.array([len(g) for g, _ in groups], dtype=float)
        probabilities /= probabilities.sum()
        exchanges = []
        for iteration in range(proposals):
            group, column = groups[rng.choice(len(groups), p=probabilities)]
            i, j = rng.choice(group, 2, replace=False)
            old = current[[i, j]].copy()
            new = old.copy()
            new[:, column] = old[::-1, column]
            if old[0, column] != old[1, column]:
                evaluator.send('evaluate', (old, new))
                metrics = evaluator.receive()
                candidate_score = score(metrics, len(origins)) if evaluator.guard(metrics) else np.inf
                if candidate_score < best-1e-10:
                    candidate = current.copy()
                    candidate[[i, j]] = new
                    if len(np.unique(candidate, axis=0)) == len(candidate):
                        current = candidate
                        best = candidate_score
                        evaluator.send('accept')
                        exchanges.append(dict(proposal=iteration+1, i=int(i), j=int(j), column=int(column), score=best))
                        if len(exchanges) % 100 == 0:
                            evaluator.send('rebase', current)
                            evaluator.receive()
            if (iteration+1) % 100 == 0 or iteration+1 == proposals:
                emit(f'{iteration+1}/{proposals}: {len(exchanges)} swaps; score {initial:.5f} -> {best:.5f}; {time.perf_counter()-started:.1f}s')
        evaluator.send('rebase', current)
        if not evaluator.guard(evaluator.receive()):
            raise RuntimeError('Final rebuilt matrix failed the condition guard; no output returned')
    finally:
        evaluator.close()
    np.testing.assert_array_equal(original[:, 1], current[:, 1])
    for group, column in groups:
        np.testing.assert_array_equal(np.sort(original[group, column]), np.sort(current[group, column]))
        fixed = 2 if column == 0 else 0
        np.testing.assert_array_equal(original[group, fixed], current[group, fixed])
    # Assign only coordinates that changed, avoiding roundoff in untouched rows.
    for column in (0, 2):
        mask = current[:, column] != original[:, column]
        values = current[mask, column] + (center_z_mm if column == 2 else 0)
        df.iloc[np.flatnonzero(mask), df.columns.get_loc(COLUMNS[column])] = values
    report = dict(method='depth_exchange_v1', workers=len(evaluator.processes), seed=int(seed), proposals=int(proposals),
                  accepted_swaps=len(exchanges), initial_score=initial, final_score=best,
                  radius_mm=float(radius_mm), center_z_mm=float(center_z_mm),
                  origins_mm=origins.tolist(), orders=list(orders), frequencies_hz=frequencies.tolist(),
                  sound_speed=float(sound_speed), exchanges=exchanges,
                  elapsed_seconds=time.perf_counter()-started)
    emit(f'Complete: {len(exchanges)} swaps in {report["elapsed_seconds"]:.1f}s')
    return df, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--radius-mm', type=float)
    parser.add_argument('--center-z-mm', type=float)
    parser.add_argument('--orders', nargs='+', type=int, default=[5, 8])
    parser.add_argument('--frequencies-hz', nargs='+', type=float)
    parser.add_argument('--sound-speed', type=float, default=343.)
    parser.add_argument('--proposals', type=int, default=1000)
    parser.add_argument('--seed', type=int, default=20261001)
    args = parser.parse_args()
    report_path = args.output.with_suffix('.optimization.json')
    if args.input.resolve() == args.output.resolve():
        parser.error('Input and output must be different files')
    if args.output.exists() or report_path.exists():
        parser.error('Output or report already exists; choose a new output name')
    result, report = optimize_grid(args.input, radius_mm=args.radius_mm,
        center_z_mm=args.center_z_mm, orders=args.orders,
        frequencies_hz=args.frequencies_hz, sound_speed=args.sound_speed,
        proposals=args.proposals, seed=args.seed, progress=lambda s: print(s, flush=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Saved {args.output}\nReport: {report_path}', flush=True)


if __name__ == '__main__':
    mp.freeze_support()
    main()


