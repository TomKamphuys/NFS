"""Deterministic centre-only depth exchanges using a GUI-owned thread pool.

Dependencies: NumPy, pandas, threadpoolctl and SciPy with sph_harm_y and batched solve_triangular.
See README_grid_optimizer_multi1.md for the GUI integration API.
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
from threadpoolctl import threadpool_limits
from scipy.linalg import solve_triangular
from scipy.special import sph_harm_y, spherical_jn, spherical_yn

COLUMNS = ["r_xy_mm", "phi_deg", "z_mm"]


def xyz(coordinates):
    r, phi, z = coordinates.T
    angle = np.deg2rad(phi)
    return np.column_stack([r*np.cos(angle), r*np.sin(angle), z])/1000


def log_frequency_grid(frequencies_per_octave=12, min_hz=3250., max_hz=20000.):
    """Return octave-spaced frequencies, including both band endpoints."""
    if (not isinstance(frequencies_per_octave, (int, np.integer))
            or frequencies_per_octave < 1):
        raise ValueError('frequencies_per_octave must be a positive integer')
    if not np.isfinite([min_hz, max_hz]).all() or min_hz <= 0 or max_hz <= min_hz:
        raise ValueError('Frequency band must be finite, positive, and increasing')
    octave_steps = np.log2(max_hz / min_hz) * frequencies_per_octave
    steps = np.arange(int(np.floor(octave_steps + 1e-12)) + 1, dtype=float)
    frequencies = min_hz * np.exp2(steps / frequencies_per_octave)
    if not np.isclose(frequencies[-1], max_hz, rtol=1e-12, atol=0.):
        frequencies = np.append(frequencies, max_hz)
    else:
        frequencies[-1] = max_hz
    return frequencies


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




def _process_eval_worker(connection, specs, frequencies, sound_speed):
    """Own one persistent process, warming imports before grid data is available."""
    try:
        with threadpool_limits(limits=1):
            matrices = None
            pending = None
            connection.send(("ready", None))
            while True:
                command, *payload = connection.recv()
                if command == "close":
                    break
                if command == "configure":
                    specs, frequencies, sound_speed = payload
                    frequencies = np.asarray(frequencies, dtype=float)
                    sound_speed = float(sound_speed)
                    matrices = None
                    pending = None
                    connection.send(("configured", None))
                    continue
                if command == "initialize":
                    coordinates = payload[0]
                    matrices = []
                    for _, (order_index, indices, order) in enumerate(specs, 1):
                        matrix = MatrixSet(
                            coordinates, order, np.zeros((1, 3)), frequencies[indices], sound_speed
                        )
                        matrices.append((order_index, indices, matrix))
                        connection.send(("initializing", len(matrices)))
                    pending = None
                    connection.send(("initialized", [(n, indices, matrix.baseline)
                                                       for n, indices, matrix in matrices]))
                    continue
                if command == "evaluate":
                    old, new = payload
                    grams = [matrix.proposal(old, new) for _, _, matrix in matrices]
                    try:
                        metrics = [
                            (order_index, indices, matrix.metrics(gram))
                            for (order_index, indices, matrix), gram in zip(matrices, grams)
                        ]
                        pending = grams
                    except np.linalg.LinAlgError:
                        metrics = None
                        pending = None
                    connection.send(("evaluated", metrics))
                elif command == "accept":
                    if pending is None or len(pending) != len(matrices):
                        raise RuntimeError("Cannot accept without a complete evaluated candidate")
                    for (_, _, matrix), gram in zip(matrices, pending):
                        matrix.gram = gram
                    pending = None
                    connection.send(("accepted", None))
                elif command == "reject":
                    pending = None
                elif command == "rebase":
                    coordinates = payload[0]
                    for _, _, matrix in matrices:
                        matrix.rebase(coordinates)
                    connection.send(("rebased", [(n, indices, matrix.metrics(matrix.gram))
                                                  for n, indices, matrix in matrices]))
    except Exception:
        try:
            connection.send(("error", traceback.format_exc()))
        except Exception:
            pass
    finally:
        connection.close()


class _ProcessEvaluator:
    """Persistent process-owned matrix shards for sequential proposal scoring."""
    def __init__(self, orders, frequencies, sound_speed, workers):
        self.orders = tuple(orders)
        self.frequencies = np.asarray(frequencies, dtype=float)
        self.sound_speed = float(sound_speed)
        self.shape = (len(orders), len(frequencies), 4)
        worker_count = max(1, min(int(workers), len(orders) * len(frequencies)))
        self.specs = self._partition_specs(self.orders, self.frequencies, worker_count)
        self.workers = len(self.specs)
        self.context = mp.get_context("spawn")
        self.connections = []
        self.processes = []
        try:
            for shard_specs in self.specs:
                parent, child = self.context.Pipe(duplex=True)
                process = self.context.Process(
                    target=_process_eval_worker,
                    args=(child, shard_specs, self.frequencies, self.sound_speed),
                    name="grid-optimizer-worker",
                )
                process.start()
                child.close()
                self.connections.append(parent)
                self.processes.append(process)
            for connection in self.connections:
                self._receive(connection)
            self.baseline = None
        except Exception:
            self.close()
            raise

    @staticmethod
    def _partition_specs(orders, frequencies, worker_count):
        cells_per_worker = np.array_split(
            np.arange(len(orders) * len(frequencies)), worker_count
        )
        partitions = []
        for cells in cells_per_worker:
            specs = []
            for order_index, order in enumerate(orders):
                indices = cells[cells // len(frequencies) == order_index] % len(frequencies)
                if len(indices):
                    specs.append((order_index, indices, order))
            partitions.append(specs)
        return partitions

    def configure(self, orders, frequencies, sound_speed):
        """Update worker frequency shards without respawning Python processes."""
        orders = tuple(orders)
        frequencies = np.asarray(frequencies, dtype=float)
        specs = self._partition_specs(orders, frequencies, self.workers)
        for connection, shard_specs in zip(self.connections, specs):
            connection.send(("configure", shard_specs, frequencies, float(sound_speed)))
        for connection in self.connections:
            status, _ = self._receive(connection)
            if status != "configured":
                raise RuntimeError(f"Unexpected worker configuration response: {status}")
        self.orders = orders
        self.frequencies = frequencies
        self.sound_speed = float(sound_speed)
        self.shape = (len(orders), len(frequencies), 4)
        self.specs = specs
        self.baseline = None

    def _receive(self, connection):
        response = connection.recv()
        if response[0] == "error":
            raise RuntimeError(response[1])
        return response

    def _assemble(self, values):
        if any(value is None for value in values):
            return None
        if len(values) != len(self.specs):
            raise RuntimeError(
                f"Optimizer returned {len(values)} worker results; expected {len(self.specs)}"
            )
        result = np.empty(self.shape)
        seen = np.zeros(self.shape[:2], dtype=np.uint8)
        for shard_specs, shard_values in zip(self.specs, values):
            if len(shard_values) != len(shard_specs):
                raise RuntimeError(
                    f"Optimizer worker returned {len(shard_values)} shard results; "
                    f"expected {len(shard_specs)}"
                )
            for (n, indices, _), (result_n, result_indices, metric) in zip(shard_specs, shard_values):
                if n != result_n:
                    raise RuntimeError("Optimizer worker returned a mismatched harmonic order")
                expected_indices = np.asarray(indices, dtype=int)
                returned_indices = np.asarray(result_indices, dtype=int)
                if not np.array_equal(returned_indices, expected_indices):
                    raise RuntimeError(
                        f"Optimizer worker returned mismatched frequency labels for order {n}: "
                        f"{returned_indices.tolist()} != {expected_indices.tolist()}"
                    )
                if metric.shape != (len(expected_indices), result.shape[2]):
                    raise RuntimeError(
                        f"Optimizer worker returned metric shape {metric.shape}; expected "
                        f"({len(expected_indices)}, {result.shape[2]})"
                    )
                if np.any(seen[result_n, expected_indices]):
                    raise RuntimeError("Optimizer returned a duplicate order/frequency result")
                result[result_n, expected_indices] = metric
                seen[result_n, expected_indices] += 1
        if not np.all(seen == 1):
            missing = np.argwhere(seen == 0).tolist()
            duplicates = np.argwhere(seen > 1).tolist()
            raise RuntimeError(
                f"Optimizer result coverage mismatch: missing={missing}, duplicates={duplicates}"
            )
        return list(result)

    def initialize(self, coordinates, progress_callback=None):
        for connection in self.connections:
            connection.send(("initialize", coordinates))
        completed_by_worker = [0] * len(self.connections)
        total = sum(len(specs) for specs in self.specs)
        responses = [None] * len(self.connections)
        remaining = set(range(len(self.connections)))
        while remaining:
            for worker_index in tuple(remaining):
                connection = self.connections[worker_index]
                if not connection.poll(0.05):
                    continue
                response = connection.recv()
                if response[0] == "error":
                    raise RuntimeError(response[1])
                if response[0] == "initializing":
                    completed_by_worker[worker_index] = response[1]
                    if progress_callback is not None:
                        progress_callback(sum(completed_by_worker), total)
                elif response[0] == "initialized":
                    responses[worker_index] = response[1]
                    remaining.remove(worker_index)
        self.baseline = self._assemble(responses)
        return self.baseline

    def evaluate(self, old, new):
        for connection in self.connections:
            connection.send(("evaluate", old, new))
        return self._assemble([self._receive(connection)[1] for connection in self.connections])

    def accept(self):
        for connection in self.connections:
            connection.send(("accept",))
        responses = [self._receive(connection) for connection in self.connections]
        if any(status != "accepted" for status, _ in responses):
            raise RuntimeError("Optimizer worker did not confirm candidate matrix acceptance")

    def reject(self):
        for connection in self.connections:
            connection.send(("reject",))

    def rebase(self, coordinates):
        for connection in self.connections:
            connection.send(("rebase", coordinates))
        return self._assemble([self._receive(connection)[1] for connection in self.connections])

    def guard(self, metrics):
        if metrics is None:
            return False
        for baseline, candidate in zip(self.baseline, metrics):
            a, b = baseline[:, 0], candidate[:, 0]
            if not (b.max() <= a.max()+1e-8 and b.mean() <= a.mean()+1e-8):
                return False
        return True

    def close(self):
        for connection in self.connections:
            try:
                connection.send(("close",))
            except (BrokenPipeError, EOFError, OSError):
                pass
        for process in self.processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
        for connection in self.connections:
            connection.close()


def optimize_grid(grid, *, radius_mm=None, center_z_mm=None,
                  orders=(6,), frequencies_hz=None, frequencies_per_octave=12,
                  sound_speed=343., proposals=1000, seed=20261001,
                  progress=print, progress_callback=None, workers=None, evaluator=None):
    """Return (new_dataframe, report); never mutate the input.

    Evaluation uses only the physical cylinder centre as the expansion origin.
    Radius and centre are read from grid_gen metadata unless explicitly supplied.
    Exchanges use positional row indices; dataframe index and all other columns
    are preserved. Progress is a callable accepting a string, or None.
    progress_callback receives dictionaries in the calling thread. Stages are
    initializing, optimizing, finalizing and complete; fraction is 0..1 and
    measures proposal progress (not a wall-time estimate). Exceptions propagate.
    """
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
    frequencies = np.asarray(
        log_frequency_grid(frequencies_per_octave) if frequencies_hz is None else frequencies_hz,
        dtype=float,
    )
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
    def notify(stage, completed=0, accepted=0, current_score=None):
        if progress_callback is not None:
            progress_callback(dict(stage=stage, completed=completed, total=proposals,
                fraction=(completed/proposals if proposals else (1.0 if stage == 'complete' else 0.0)),
                accepted_swaps=accepted, elapsed_seconds=time.perf_counter()-started,
                score=current_score))

    def emit(message):
        if progress is not None:
            progress(message)
    worker_count = max(1, int(workers if workers is not None else (os.cpu_count() or 1) // 2))
    owns_evaluator = evaluator is None
    if evaluator is not None and (
        evaluator.orders != orders
        or not np.array_equal(evaluator.frequencies, frequencies)
        or evaluator.sound_speed != float(sound_speed)
    ):
        raise ValueError("The supplied process evaluator has different optimizer settings")
    emit(f'Initialising {len(current)} points, orders {orders}, {len(frequencies)} frequencies; using {worker_count if owns_evaluator else evaluator.workers} process workers')
    notify('initializing')
    with threadpool_limits(limits=1):
        if owns_evaluator:
            evaluator = _ProcessEvaluator(orders, frequencies, sound_speed, worker_count)
        try:
            matrix_total = len(orders) * len(frequencies)
            if progress_callback is not None:
                progress_callback(dict(
                    stage='initializing', completed=0, total=matrix_total,
                    fraction=0.0, elapsed_seconds=time.perf_counter()-started,
                ))
            evaluator.initialize(
                current,
                progress_callback=(
                    lambda completed, total: progress_callback(dict(
                        stage='initializing', completed=completed, total=total,
                        fraction=(completed / total if total else 1.0),
                        elapsed_seconds=time.perf_counter()-started,
                    )) if progress_callback is not None else None
                ),
            )
            initial = best = score(evaluator.baseline, 1)
            rng = np.random.Generator(np.random.PCG64(seed))
            probabilities = np.array([len(g) for g, _ in groups], dtype=float)
            probabilities /= probabilities.sum()
            exchanges = []
            notify('optimizing', current_score=best)
            for iteration in range(proposals):
                group, column = groups[rng.choice(len(groups), p=probabilities)]
                i, j = rng.choice(group, 2, replace=False)
                old = current[[i, j]].copy()
                new = old.copy()
                new[:, column] = old[::-1, column]
                accepted = False
                if old[0, column] != old[1, column]:
                    metrics = evaluator.evaluate(old, new)
                    candidate_score = score(metrics, 1) if evaluator.guard(metrics) else np.inf
                    if candidate_score < best-1e-10:
                        candidate = current.copy()
                        candidate[[i, j]] = new
                        if len(np.unique(candidate, axis=0)) == len(candidate):
                            current, best = candidate, candidate_score
                            evaluator.accept()
                            accepted = True
                            exchanges.append(dict(proposal=iteration+1, i=int(i), j=int(j),
                                                  column=int(column), score=best))
                            if len(exchanges) % 100 == 0:
                                evaluator.rebase(current)
                    if not accepted:
                        evaluator.reject()
                completed = iteration + 1
                notify('optimizing', completed, len(exchanges), best)
                if completed % 100 == 0 or completed == proposals:
                    emit(f'{completed}/{proposals}: {len(exchanges)} swaps; score {initial:.5f} -> {best:.5f}; {time.perf_counter()-started:.1f}s')
            notify('finalizing', proposals, len(exchanges), best)
            if not evaluator.guard(evaluator.rebase(current)):
                raise RuntimeError('Final rebuilt matrix failed the condition guard; no output returned')
        finally:
            if owns_evaluator:
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
    active_workers = evaluator.workers
    report = dict(method='depth_exchange_v1', workers=active_workers,
                  execution='multiprocessing',
                  seed=int(seed), proposals=int(proposals),
                  accepted_swaps=len(exchanges), initial_score=initial, final_score=best,
                  radius_mm=float(radius_mm), center_z_mm=float(center_z_mm),
                  origins_mm=origins.tolist(), orders=list(orders), frequencies_hz=frequencies.tolist(),
                  sound_speed=float(sound_speed), exchanges=exchanges,
                  elapsed_seconds=time.perf_counter()-started)
    emit(f'Complete: {len(exchanges)} swaps in {report["elapsed_seconds"]:.1f}s')
    notify('complete', proposals, len(exchanges), best)
    return df, report


def _emit_service_event(events_path, event):
    with events_path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event) + '\n')
        stream.flush()


def run_service(service_dir, *, workers=None, orders=(6,), frequencies_hz=None,
                frequencies_per_octave=12, sound_speed=343.):
    """Keep one warm calculation pool alive while the GUI pane is open."""
    service_dir = Path(service_dir)
    service_dir.mkdir(parents=True, exist_ok=True)
    request_path = service_dir / 'request.json'
    events_path = service_dir / 'events.jsonl'
    if events_path.exists():
        events_path.unlink()
    frequencies = np.asarray(
        log_frequency_grid(frequencies_per_octave) if frequencies_hz is None else frequencies_hz,
        dtype=float,
    )
    worker_capacity = max(1, int(workers if workers is not None else (os.cpu_count() or 1) // 2))
    orders = tuple(orders)
    evaluator = _ProcessEvaluator(orders, frequencies, sound_speed, worker_capacity)
    _emit_service_event(events_path, {"event": "service_ready", "workers": evaluator.workers})
    try:
        while True:
            if not request_path.exists():
                time.sleep(0.05)
                continue
            try:
                request = json.loads(request_path.read_text(encoding='utf-8'))
            finally:
                request_path.unlink(missing_ok=True)
            if request.get("command") == "shutdown":
                break
            if request.get("command") != "optimize":
                _emit_service_event(events_path, {
                    "event": "job_failed", "job_id": request.get("job_id"),
                    "error": "Unknown optimizer service command",
                })
                continue
            job_id = request.get("job_id")
            try:
                job_orders = tuple(int(order) for order in request.get("orders", orders))
                job_frequencies = np.asarray(
                    request.get("frequencies_hz")
                    if request.get("frequencies_hz") is not None
                    else log_frequency_grid(int(request.get(
                        "frequencies_per_octave", frequencies_per_octave
                    ))),
                    dtype=float,
                )
                if evaluator is None:
                    evaluator = _ProcessEvaluator(
                        job_orders, job_frequencies, sound_speed, worker_capacity
                    )
                elif (evaluator.orders != job_orders
                      or not np.array_equal(evaluator.frequencies, job_frequencies)
                      or evaluator.sound_speed != float(sound_speed)):
                    evaluator.configure(job_orders, job_frequencies, sound_speed)
                result, report = optimize_grid(
                    request["input"],
                    orders=job_orders,
                    frequencies_hz=job_frequencies,
                    sound_speed=sound_speed,
                    proposals=int(request.get("proposals", 1000)),
                    seed=int(request.get("seed", 20261001)),
                    workers=evaluator.workers,
                    evaluator=evaluator,
                    progress=None,
                    progress_callback=lambda event: _emit_service_event(
                        events_path, {"event": "progress", "job_id": job_id, **event}
                    ),
                )
                output_path = Path(request["output"])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                result.to_csv(output_path, index=False)
                report_path = output_path.with_suffix('.optimization.json')
                report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
                _emit_service_event(events_path, {
                    "event": "job_complete", "job_id": job_id,
                    "output": str(output_path), "report": str(report_path),
                })
            except Exception as exc:
                _emit_service_event(events_path, {
                    "event": "job_failed", "job_id": job_id,
                    "error": f"{exc}\n{traceback.format_exc()}",
                })
    finally:
        if evaluator is not None:
            evaluator.close()
        _emit_service_event(events_path, {"event": "service_closed"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, nargs='?')
    parser.add_argument('output', type=Path, nargs='?')
    parser.add_argument('--radius-mm', type=float)
    parser.add_argument('--center-z-mm', type=float)
    parser.add_argument('--orders', nargs='+', type=int, default=[6])
    parser.add_argument('--frequencies-hz', nargs='+', type=float)
    parser.add_argument('--frequencies-per-octave', type=int, default=12)
    parser.add_argument('--sound-speed', type=float, default=343.)
    parser.add_argument('--proposals', type=int, default=1000)
    parser.add_argument('--seed', type=int, default=20261001)
    parser.add_argument('--workers', type=int, default=None)
    parser.add_argument('--progress-jsonl', action='store_true',
                        help='Write structured progress events to stdout, one JSON object per line')
    parser.add_argument('--progress-file', type=Path,
                        help='Append structured progress events to this JSONL file')
    parser.add_argument('--service-dir', type=Path,
                        help='Run as a persistent GUI service using request/events JSON files')
    args = parser.parse_args()
    if args.service_dir is not None:
        run_service(args.service_dir, workers=args.workers, orders=args.orders,
                    frequencies_hz=args.frequencies_hz,
                    frequencies_per_octave=args.frequencies_per_octave,
                    sound_speed=args.sound_speed)
        return
    if args.input is None or args.output is None:
        parser.error('input and output are required unless --service-dir is specified')
    report_path = args.output.with_suffix('.optimization.json')
    if args.input.resolve() == args.output.resolve():
        parser.error('Input and output must be different files')
    if args.output.exists() or report_path.exists():
        parser.error('Output or report already exists; choose a new output name')
    def report_progress(event):
        message = json.dumps({"event": "progress", **event})
        if args.progress_file is not None:
            with args.progress_file.open('a', encoding='utf-8') as stream:
                stream.write(message + '\n')
                stream.flush()
        elif args.progress_jsonl:
            print(message, flush=True)

    result, report = optimize_grid(args.input, radius_mm=args.radius_mm,
        center_z_mm=args.center_z_mm, orders=args.orders,
        frequencies_hz=args.frequencies_hz,
        frequencies_per_octave=args.frequencies_per_octave, sound_speed=args.sound_speed,
        proposals=args.proposals, seed=args.seed, workers=args.workers,
        progress=None if args.progress_jsonl or args.progress_file is not None else lambda s: print(s, flush=True),
        progress_callback=report_progress if args.progress_jsonl or args.progress_file is not None else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    if args.progress_jsonl:
        print(json.dumps({"event": "saved", "output": str(args.output),
                          "report": str(report_path)}), flush=True)
    elif args.progress_file is None:
        print(f'Saved {args.output}\nReport: {report_path}', flush=True)


if __name__ == '__main__':
    mp.freeze_support()
    main()


