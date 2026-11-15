"""Pane-owned review processes, warmed once and limited to one numerical thread each."""
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os
import threading


def review_worker_count():
    count = max(1, (os.cpu_count() or 1) // 2)
    # ProcessPoolExecutor uses Windows wait handles, which support at most 61 workers.
    return min(count, 61) if os.name == 'nt' else count


_NUMERICAL_LIMIT = None


def _initialize_worker(barrier, closing):
    global _NUMERICAL_LIMIT
    for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
                'BLIS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[key] = '1'
    import numpy as np
    from scipy.fft import rfft
    from threadpoolctl import threadpool_limits
    from .measurement_review import inspect_point
    # Environment variables alone do not limit libraries imported before an initializer.
    _NUMERICAL_LIMIT = threadpool_limits(limits=1)
    np.linalg.lstsq(np.eye(4), np.ones((4, 8)), rcond=None)
    rfft(np.ones(1024))
    xyz = np.random.default_rng(0).normal(size=(13, 3))
    inspect_point(xyz, np.ones((13, 37), complex), 0, np.arange(13)[None, :])
    # No worker can serve warmup jobs until every process has imported and warmed.
    try:
        barrier.wait(timeout=60)
    except threading.BrokenBarrierError:
        # Closing the pane during startup aborts the barrier to release new workers.
        if not closing.is_set():
            raise


def worker_details():
    """Small diagnostic task, also used to force eager process startup."""
    from threadpoolctl import threadpool_info
    return os.getpid(), threadpool_info()


class ReviewProcessPool:
    def __init__(self, workers=None):
        self.workers = review_worker_count() if workers is None else max(1, workers)
        self._lock = threading.Lock()
        self._executor = None
        self._barrier = None
        self._closing = None
        self._warmup = None
        self._closed = False

    def warm(self):
        """Call off the GUI thread; concurrent scans wait for this same warmup."""
        with self._lock:
            if self._closed:
                raise InterruptedError('Review pool closed.')
            if self._executor is None:
                context = multiprocessing.get_context('spawn')
                self._barrier = context.Barrier(self.workers)
                self._closing = context.Event()
                self._executor = ProcessPoolExecutor(
                    max_workers=self.workers, mp_context=context,
                    initializer=_initialize_worker, initargs=(self._barrier, self._closing))
                try:
                    self._warmup = [self._executor.submit(worker_details) for _ in range(self.workers)]
                except BaseException:
                    self._closed = True
                    self._closing.set()
                    self._barrier.abort()
                    self._executor.shutdown(wait=False, cancel_futures=True)
                    self._executor = None
                    raise
            futures = self._warmup
        for future in futures:
            future.result()

    def map(self, function, tasks, cancelled=lambda: False):
        """Ordered, bounded submissions; completed scans never shut down the pool."""
        if cancelled():
            raise InterruptedError('Scan cancelled.')
        self.warm()
        iterator = iter(tasks)
        while True:
            if cancelled():
                raise InterruptedError('Scan cancelled.')
            with self._lock:
                if self._closed:
                    raise InterruptedError('Review pool closed.')
                futures = []
                for _ in range(self.workers):
                    try:
                        task = next(iterator)
                    except StopIteration:
                        break
                    futures.append(self._executor.submit(function, task))
            if not futures:
                return
            try:
                for future in futures:
                    if cancelled():
                        raise InterruptedError('Scan cancelled.')
                    yield future.result()
            finally:
                for future in futures:
                    future.cancel()

    def close(self):
        with self._lock:
            self._closed = True
            executor, self._executor = self._executor, None
            if self._barrier is not None:
                self._closing.set()
                self._barrier.abort()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
