"""Read-only full-scan benchmark using the pane's persistent, single-threaded workers."""
import argparse
import csv
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('dataset', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
    from nfs.measurement_review import scan_directory
    from nfs.review_pool import ReviewProcessPool, worker_details
    folder = args.dataset/'measurement_set' if (args.dataset/'measurement_set').is_dir() else args.dataset
    pool = ReviewProcessPool()
    result = dict(workers=pool.workers, numerical_threads_per_worker=1, scan_seconds=[])
    try:
        started = time.perf_counter()
        pool.warm()
        result['warmup_seconds'] = time.perf_counter()-started
        pids = set(pool._executor._processes)
        for attempt in range(2):
            started = time.perf_counter()
            rows = scan_directory(folder, pool=pool)
            result['scan_seconds'].append(time.perf_counter()-started)
            assert set(pool._executor._processes) == pids
            print(f'Scan {attempt+1}: {len(rows)} recordings in {result["scan_seconds"][-1]:.2f}s', flush=True)
            actual = {row['filename']: (row['status'], row['score'],
                      sorted(issue['code'] for issue in row.get('issues', []))) for row in rows}
            if attempt:
                assert actual == previous
            previous = actual
        if args.baseline:
            baseline = list(csv.DictReader(args.baseline.open(encoding='utf-8')))
            expected = {row['filename']: (row['status'], float(row['score']),
                        sorted(issue['code'] for issue in json.loads(row['evidence'])['issues'])) for row in baseline}
            assert actual == expected
            result['matches_baseline'] = True
        details = [future.result() for future in
                   [pool._executor.submit(worker_details) for _ in range(pool.workers)]]
        assert all(info['num_threads'] == 1 for _, libraries in details for info in libraries)
        result.update(recordings=len(rows), same_workers_reused=True, rescans_match=True,
                      numerical_thread_limits_verified=True)
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
    finally:
        pool.close()


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
