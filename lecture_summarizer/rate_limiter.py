from __future__ import annotations
import asyncio
import time
from collections import deque
from dataclasses import dataclass
from threading import Condition, Lock


@dataclass
class TokenBucket:
    per_minute: int
    window_sec: int = 60
    label: str | None = None

    def __post_init__(self):
        if self.per_minute <= 0:
            raise ValueError("per_minute must be a positive integer")
        if self.window_sec <= 0:
            raise ValueError("window_sec must be a positive integer")
        self._times: deque[float] = deque()
        self._lock = Lock()
        self._cond = Condition(self._lock)
        self._last_warning = 0.0

    def _prune(self, now: float) -> None:
        """Drop timestamps that fall outside the current window."""
        while self._times and now - self._times[0] >= self.window_sec:
            self._times.popleft()

    def _reserve_token(self, now: float) -> bool:
        if len(self._times) < self.per_minute:
            self._times.append(now)
            self._cond.notify_all()
            return True
        return False

    def acquire(self):
        """Block until a token is available, then consume it."""
        with self._cond:
            while True:
                now = time.monotonic()
                self._prune(now)
                if self._reserve_token(now):
                    return
                wait_for = self.window_sec - (now - self._times[0])
                if wait_for <= 0:
                    continue
                self._cond.wait(timeout=min(wait_for, 0.5))

    async def acquire_async(self):
        """Async-friendly variant of acquire()."""
        while True:
            with self._cond:
                now = time.monotonic()
                self._prune(now)
                if self._reserve_token(now):
                    return
                wait_for = self.window_sec - (now - self._times[0])
            if wait_for <= 0:
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(min(wait_for, 0.5))

    def utilization(self) -> float:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            if self.per_minute <= 0:
                return 0.0
            return min(len(self._times) / self.per_minute, 1.0)
