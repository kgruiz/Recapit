import threading

import pytest

from recapit.api import _run_parallel


def test_run_parallel_executes_concurrently():
    barrier = threading.Barrier(3)
    seen = []

    def worker(item):
        seen.append(item)
        try:
            barrier.wait(timeout=1)
        except threading.BrokenBarrierError as exc:
            pytest.fail(f"Barrier broken; worker {item} may not have executed concurrently: {exc}")

    _run_parallel([1, 2, 3], max_workers=3, fn=worker)
    assert sorted(seen) == [1, 2, 3]
