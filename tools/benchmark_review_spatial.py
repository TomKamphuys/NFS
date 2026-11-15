"""Benchmark only LF fitting, including cold process startup; never modifies recordings."""
import os
for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
import argparse
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('dataset', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
    import numpy as np
    from scipy.spatial import cKDTree
    from nfs.measurement_review import features, coordinates, compare_spatial
    folder = args.dataset/'measurement_set' if (args.dataset/'measurement_set').is_dir() else args.dataset
    paths = sorted(folder.glob('*_ir.wav'))
    xyz = np.array([coordinates(path.name)[0] for path in paths])
    response = np.array([features(path)[0] for path in paths])
    near = cKDTree(xyz).query(xyz, k=13)[1]
    timings = {}
    scores = None
    for workers in (1, 4):
        started = time.perf_counter()
        rows = list(compare_spatial(xyz, response, near, workers=workers))
        timings[str(workers)] = time.perf_counter()-started
        actual = np.array([row['score'] for row in rows])
        if scores is not None:
            np.testing.assert_allclose(actual, scores, rtol=1e-12)
        scores = actual
    result = dict(recordings=len(paths), seconds=timings, includes_process_startup=True,
                  scores_match=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
