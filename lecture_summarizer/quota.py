from __future__ import annotations

import logging
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Deque, Dict, Iterable, Mapping

logger = logging.getLogger(__name__)


@dataclass
class QuotaConfig:
    request_limits: Mapping[str, int]
    token_limits: Mapping[str, int]
    rpm_warn_threshold: float = 0.8
    rpm_sleep_threshold: float = 0.9
    token_warn_threshold: float = 0.8
    storage_limit_bytes: int = 20 * 1024 * 1024 * 1024  # 20 GB
    upload_limit_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GB per upload
    concurrency_limit: int = 100
    warn_cooldown_seconds: float = 10.0
    max_preemptive_sleep: float = 0.5


class QuotaMonitor:
    def __init__(self, config: QuotaConfig) -> None:
        self._config = config
        self._lock = Lock()
        self._token_windows: Dict[str, Deque[tuple[float, int]]] = {}
        self._token_warned_at: Dict[str, float] = {}
        self._rpm_warned_at: Dict[str, float] = {}
        self._uploaded_bytes: int = 0
        self._active_uploads: int = 0

    def check_rpm(self, model: str, utilization: float, per_minute: int, window_seconds: int) -> None:
        now = time.monotonic()
        sleep_duration = 0.0
        with self._lock:
            if utilization >= self._config.rpm_warn_threshold:
                last_warn = self._rpm_warned_at.get(model, 0.0)
                if now - last_warn >= self._config.warn_cooldown_seconds:
                    logger.warning(
                        "Model %s request rate at %.0f%% of per-minute quota (%d RPM)",
                        model,
                        utilization * 100.0,
                        per_minute,
                    )
                    self._rpm_warned_at[model] = now
            if utilization >= self._config.rpm_sleep_threshold:
                sleep_duration = min(window_seconds / max(per_minute, 1), self._config.max_preemptive_sleep)
        if sleep_duration > 0:
            time.sleep(sleep_duration)

    def register_event(self, *, model: str, timestamp: float, total_tokens: int | None) -> None:
        if total_tokens is None:
            return
        limit = self._config.token_limits.get(model)
        if not limit:
            return
        with self._lock:
            window = self._token_windows.setdefault(model, deque())
            window.append((timestamp, total_tokens))
            cutoff = timestamp - 60.0
            while window and window[0][0] < cutoff:
                window.popleft()
            window_total = sum(tokens for _, tokens in window)
            utilization = window_total / limit
            if utilization >= self._config.token_warn_threshold:
                last_warn = self._token_warned_at.get(model, 0.0)
                if timestamp - last_warn >= self._config.warn_cooldown_seconds:
                    logger.warning(
                        "Model %s token usage at %.0f%% of per-minute quota (%d tokens/min)",
                        model,
                        utilization * 100.0,
                        limit,
                    )
                    self._token_warned_at[model] = timestamp

    @contextmanager
    def track_upload(self, *, path: str, size_bytes: int) -> Iterable[None]:
        self._before_upload(path=path, size_bytes=size_bytes)
        try:
            yield
        finally:
            self._after_upload(size_bytes=size_bytes)

    def _before_upload(self, *, path: str, size_bytes: int) -> None:
        if size_bytes > self._config.upload_limit_bytes:
            raise ValueError(
                f"Upload {path} exceeds per-file upload limit of {self._config.upload_limit_bytes // (1024 * 1024 * 1024)} GB"
            )
        with self._lock:
            self._uploaded_bytes += size_bytes
            self._active_uploads += 1
            storage_util = self._uploaded_bytes / self._config.storage_limit_bytes
            if storage_util >= self._config.token_warn_threshold:
                logger.warning(
                    "Uploads this run total %.2f GB (%.0f%% of 20 GB Files API window)",
                    self._uploaded_bytes / (1024**3),
                    storage_util * 100.0,
                )
            concurrency_util = self._active_uploads / max(self._config.concurrency_limit, 1)
            if concurrency_util >= self._config.token_warn_threshold:
                logger.warning(
                    "Concurrent uploads at %d/%d (%.0f%% of limit)",
                    self._active_uploads,
                    self._config.concurrency_limit,
                    concurrency_util * 100.0,
                )

    def _after_upload(self, *, size_bytes: int) -> None:
        with self._lock:
            self._active_uploads = max(self._active_uploads - 1, 0)
