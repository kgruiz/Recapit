import time
from collections import deque
from dataclasses import dataclass


@dataclass
class TokenBucket:
    per_minute: int
    window_sec: int = 60

    def __post_init__(self):
        self._times = deque()

    def acquire(self):
        now = time.time()
        # drop old tokens
        while self._times and now - self._times[0] >= self.window_sec:
            self._times.popleft()
        if len(self._times) >= self.per_minute:
            sleep_for = self.window_sec - (now - self._times[0])
            target = now + max(sleep_for, 0)
            while True:
                remaining = target - time.time()
                if remaining <= 0:
                    break
                time.sleep(min(0.25, remaining))
        self._times.append(time.time())
