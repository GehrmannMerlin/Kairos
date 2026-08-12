"""M-16 scoped 测试：Retry Storm 防御（TEST 5）。

并发 Provider 429：attempt 有上限、wake-up 带 jitter 不集中同刻、Retry-After 被
尊重；auth 永不 retry。全部用 fake exceptions + 确定性 jitter，不真实轰炸 Provider。
"""

from __future__ import annotations

import asyncio

import pytest
from app.reliability.errors import ErrorClass
from app.reliability.provider_limit import (
    ProviderLimiter,
    call_with_provider_retry,
    call_with_provider_retry_delay,
)


class _FakeRateLimited(Exception):
    pass


class _FakeAuth(Exception):
    pass


def _rate_limited(exc: Exception) -> ErrorClass:
    return ErrorClass.RATE_LIMITED


def _auth(exc: Exception) -> ErrorClass:
    return ErrorClass.AUTH_FAILED


def test_provider_429_respects_retry_after() -> None:
    calls: list[int] = []
    limiter = ProviderLimiter(min_interval_seconds=0.001, max_burst=10, key="k")

    async def _fn():
        calls.append(len(calls))
        if len(calls) < 3:
            raise _FakeRateLimited()
        return "ok"

    async def _run():
        return await call_with_provider_retry(
            limiter=limiter,
            fn=_fn,
            max_attempts=3,
            error_class_fn=_rate_limited,
            retry_after_fn=lambda exc: 0.005,  # 服务端 Retry-After
            rand=lambda: 0.0,  # 确定性 jitter
        )

    out = asyncio.run(_run())
    assert out == "ok"
    assert len(calls) == 3  # 有界（max_attempts=3）


def test_auth_error_never_retried() -> None:
    calls: list[int] = []
    limiter = ProviderLimiter(min_interval_seconds=0.0, max_burst=10, key="k")

    async def _fn():
        calls.append(1)
        raise _FakeAuth()

    async def _run():
        with pytest.raises(_FakeAuth):
            await call_with_provider_retry(
                limiter=limiter, fn=_fn, max_attempts=3, error_class_fn=_auth
            )

    asyncio.run(_run())
    assert len(calls) == 1  # 无 retry storm


def test_wakeup_spread_with_jitter() -> None:
    """并发 429：各 waiter 延迟带 jitter，不集中同刻醒来。"""
    delays = [
        call_with_provider_retry_delay(attempt=0, retry_after=5.0, rand=lambda i=i: i / 10)
        for i in range(5)
    ]
    assert len(set(delays)) > 1  # jitter 产生差异化 wake-up
    assert min(delays) >= 5.0  # 尊重 Retry-After 下限


def test_throttle_key_is_metadata_hash_not_secret() -> None:
    from app.reliability.provider_limit import ThrottleKey

    key_a = ThrottleKey(family="deepseek", config_id=7, user_id=1)
    key_b = ThrottleKey(family="deepseek", config_id=8, user_id=1)
    assert key_a.fingerprint() != key_b.fingerprint()  # 不同用户 Key → 不共享
    assert "deepseek" not in key_a.fingerprint()  # 不含明文 metadata
