"""Login rate limiting.

M-02 uses a lightweight in-memory sliding-window limiter that is correct for a
single-instance dev deployment and exposes a small protocol so it can be swapped
for a shared (e.g. Redis-backed) implementation in M-16. No Redis in M-02.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol


class LoginRateLimiter(Protocol):
    def is_blocked(self, key: str) -> bool: ...
    def record_failure(self, key: str) -> None: ...
    def reset(self, key: str) -> None: ...


class InMemoryLoginLimiter:
    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> None:
        cutoff = now - self._window_seconds
        window = self._failures.get(key)
        if window is None:
            return
        kept = [ts for ts in window if ts > cutoff]
        if kept:
            self._failures[key] = kept
        else:
            self._failures.pop(key, None)

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            self._prune(key, self._clock())
            return len(self._failures.get(key, [])) >= self._max_attempts

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._prune(key, self._clock())
            self._failures.setdefault(key, []).append(self._clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
