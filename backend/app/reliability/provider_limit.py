"""M-16 Provider 限流（429 Retry-After / bounded backoff + jitter）。

Throttle key 用安全 metadata（family + credential/config id + user id 的 sha256），
绝不使用明文 API Key（D-023 密钥隔离 / §42）。auth/quota 不重试；NETWORK 按
transient 策略。限流状态 per-process（min-interval + burst）；跨 worker 的重试风暴
由「有界 attempt + 全抖动 + 服务端 Retry-After」联合防御（TEST 5）。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.reliability.errors import ErrorClass
from app.reliability.retry import RetryDecision, decide_retry


@dataclass(frozen=True)
class ThrottleKey:
    family: str
    config_id: int = 0
    user_id: int = 0

    def fingerprint(self) -> str:
        raw = f"{self.family}:{self.config_id}:{self.user_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class ProviderLimiter:
    """per-(family, config, user) 最小间隔 + burst 门控（进程内，key 为 metadata hash）。"""

    def __init__(self, *, min_interval_seconds: float, max_burst: int, key: str) -> None:
        self._min_interval = min_interval_seconds
        self._max_burst = max_burst
        self._key = key
        self._lock = asyncio.Lock()
        self._last_call_at = 0.0
        self._burst = 0
        self.recent_decisions: list[RetryDecision] = []

    async def acquire(self) -> None:
        import time

        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._last_call_at + self._min_interval - now)
            if wait > 0:
                await asyncio.sleep(wait)
            if self._burst >= self._max_burst:
                await asyncio.sleep(self._min_interval * 2)
                self._burst = 0
            self._last_call_at = time.monotonic() + wait
            self._burst += 1


async def call_with_provider_retry(
    *,
    limiter: ProviderLimiter,
    fn: Callable[[], Awaitable],
    max_attempts: int,
    error_class_fn: Callable[[Exception], ErrorClass],
    retry_after_fn: Callable[[Exception], float | None] | None = None,
    base_delay_seconds: float = 2.0,
    rand: Callable[[], float] | None = None,
) -> Any:
    """有界 provider 调用：429→Retry-After/backoff+jitter；auth/quota 直抛不重试。"""
    attempt = 0
    while True:
        await limiter.acquire()
        try:
            return await fn()
        except Exception as exc:
            ec = error_class_fn(exc)
            retry_after = retry_after_fn(exc) if retry_after_fn else None
            d = decide_retry(
                error_class=ec,
                attempt=attempt,
                max_attempts=max_attempts,
                retry_after_seconds=retry_after,
                base_delay_seconds=base_delay_seconds,
                rand=rand,
            )
            limiter.recent_decisions.append(d)
            if not d.should_retry:
                raise
            await asyncio.sleep(d.delay_seconds)
            attempt += 1


def call_with_provider_retry_delay(
    *, attempt: int, retry_after: float, rand: Callable[[], float]
) -> float:
    """TEST 5 用：暴露 retry-after + jitter 的延迟计算（确定性）。"""
    d = decide_retry(
        error_class=ErrorClass.RATE_LIMITED,
        attempt=attempt,
        max_attempts=10,
        retry_after_seconds=retry_after,
        rand=rand,
    )
    return d.delay_seconds
