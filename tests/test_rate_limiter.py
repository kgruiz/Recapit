import threading
import time

from lecture_summarizer.rate_limiter import TokenBucket


def test_token_bucket_blocks_when_limit_exceeded():
    bucket = TokenBucket(per_minute=2, window_sec=1)

    bucket.acquire()
    bucket.acquire()

    unblock_time = {}

    def acquire_and_record():
        start = time.monotonic()
        bucket.acquire()
        unblock_time["elapsed"] = time.monotonic() - start

    worker = threading.Thread(target=acquire_and_record)
    worker.start()

    time.sleep(0.1)
    assert worker.is_alive(), "third acquisition should block until the window resets"

    worker.join(timeout=2)
    assert not worker.is_alive(), "worker should unblock after the window duration"
    assert unblock_time["elapsed"] >= 0.9, "acquire should wait for roughly one second"
