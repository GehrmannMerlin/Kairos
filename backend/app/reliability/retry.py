"""M-16 RetryDecision：统一的「分类 → 恢复策略」决策，禁止调用点各处写 if 429。

D-013：先分类错误，再执行对应恢复策略。所有边界（transient backoff /
Retry-After / auth-quota user action / correction-change 守卫 / 资源等待）都在
这里收敛；调用点只调用 decide_retry。
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from app.reliability.errors import ErrorClass

_JITTER_FULL = 1.0
_BACKOFF_CAP_SECONDS = 30.0


class RetryStrategy(StrEnum):
    NONE = "none"
    TRANSIENT_BACKOFF = "transient_backoff"
    RESPECT_RETRY_AFTER = "respect_retry_after"
    CORRECTION = "correction"
    WAIT_RESOURCE = "wait_resource"
    USER_ACTION = "user_action"


@dataclass(frozen=True)
class RetryDecision:
    error_class: ErrorClass
    should_retry: bool
    strategy: RetryStrategy
    delay_seconds: float
    attempt: int
    max_attempts: int
    reason: str
    requires_change: bool = False
    blocking_action: str | None = None
    retry_after_seconds: float | None = None


def jitter_seconds(base: float, *, rand: Callable[[], float] | None = None) -> float:
    """base + [0,1) 全抖动（retry storm 防御）。rand 注入 → 测试确定性。"""
    r = rand() if rand is not None else random.random()
    return base + (r * _JITTER_FULL)


def _backoff_delay(base: float, attempt: int) -> float:
    return min(base * (2 ** max(0, attempt - 1)), _BACKOFF_CAP_SECONDS)


def correction_fingerprint(
    *, inputs: dict, tool: str, parameters: dict, environment: dict
) -> str:
    """纠错指纹：input/tool/parameter/environment 任一变化 → 新指纹。

    防 LLM 同样输入无限再试：相同指纹的重试被 decide_retry 拒绝。
    """
    canonical = json.dumps(
        {"inputs": inputs, "tool": tool, "parameters": parameters, "environment": environment},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def decide_retry(
    *,
    error_class: ErrorClass,
    attempt: int,
    max_attempts: int,
    retry_after_seconds: float | None = None,
    correction_fp: str | None = None,
    prior_correction_fp: str | None = None,
    base_delay_seconds: float = 2.0,
    rand: Callable[[], float] | None = None,
) -> RetryDecision:
    """单一决策入口：ErrorClass → 恢复策略。所有边界都在这里，调用点不再写 if。

    attempt 表示已经消耗的重试次数（首次失败为 0）。max_attempts 为总尝试上限，
    因此 attempt < max_attempts - 1 才允许继续重试（有界，永不无限）。
    """
    remaining = attempt < max_attempts - 1

    if error_class is ErrorClass.RESOURCE_UNAVAILABLE:
        return RetryDecision(
            error_class=error_class,
            should_retry=True,
            strategy=RetryStrategy.WAIT_RESOURCE,
            delay_seconds=jitter_seconds(retry_after_seconds or base_delay_seconds, rand=rand),
            attempt=attempt,
            max_attempts=max_attempts,
            reason="resource slot unavailable",
        )

    if error_class is ErrorClass.RATE_LIMITED and retry_after_seconds is not None:
        return RetryDecision(
            error_class=error_class,
            should_retry=remaining,
            strategy=RetryStrategy.RESPECT_RETRY_AFTER,
            delay_seconds=jitter_seconds(retry_after_seconds, rand=rand),
            attempt=attempt,
            max_attempts=max_attempts,
            reason="respect Retry-After",
            retry_after_seconds=retry_after_seconds,
        )

    if error_class in (
        ErrorClass.NETWORK_TIMEOUT,
        ErrorClass.TRANSIENT_SERVICE_ERROR,
        ErrorClass.DOMAIN_UNAVAILABLE,
    ):
        return RetryDecision(
            error_class=error_class,
            should_retry=remaining,
            strategy=RetryStrategy.TRANSIENT_BACKOFF,
            delay_seconds=jitter_seconds(_backoff_delay(base_delay_seconds, attempt), rand=rand),
            attempt=attempt,
            max_attempts=max_attempts,
            reason="transient backoff",
        )

    if error_class is ErrorClass.RATE_LIMITED:
        return RetryDecision(
            error_class=error_class,
            should_retry=remaining,
            strategy=RetryStrategy.TRANSIENT_BACKOFF,
            delay_seconds=jitter_seconds(_backoff_delay(base_delay_seconds, attempt), rand=rand),
            attempt=attempt,
            max_attempts=max_attempts,
            reason="rate limited without retry-after",
        )

    if error_class in (ErrorClass.AUTH_FAILED, ErrorClass.QUOTA_EXHAUSTED):
        return RetryDecision(
            error_class=error_class,
            should_retry=False,
            strategy=RetryStrategy.USER_ACTION,
            delay_seconds=0.0,
            attempt=attempt,
            max_attempts=max_attempts,
            reason="requires user action, no automatic retry",
            blocking_action="credential_or_quota_review",
        )

    if error_class in (
        ErrorClass.STRUCTURE_CHANGED,
        ErrorClass.EXTRACTION_FAILED,
        ErrorClass.QUALITY_FAILED,
    ):
        changed = bool(correction_fp) and correction_fp != prior_correction_fp
        return RetryDecision(
            error_class=error_class,
            should_retry=remaining and changed,
            strategy=RetryStrategy.CORRECTION,
            delay_seconds=jitter_seconds(base_delay_seconds, rand=rand),
            attempt=attempt,
            max_attempts=max_attempts,
            reason=(
                "correction retry" if changed else "correction retry requires strategy change"
            ),
            requires_change=not changed,
        )

    return RetryDecision(
        error_class=error_class,
        should_retry=False,
        strategy=RetryStrategy.NONE,
        delay_seconds=0.0,
        attempt=attempt,
        max_attempts=max_attempts,
        reason="non retryable",
    )


@dataclass(frozen=True)
class RetryBudget:
    """URL/Node/Domain/Task 级重试预算（attempt 数，含首次）。"""

    url_max_attempts: int
    node_max_attempts: int
    domain_max_attempts: int
    task_max_attempts: int


def retry_budget_from(runtime_limits, capacity_config) -> RetryBudget:
    """URL 级优先取 RuntimeLimits.max_retries_per_url，否则默认；其余取 CapacityConfig。"""
    default = capacity_config.default_retry_max_attempts
    url_retries = getattr(runtime_limits, "max_retries_per_url", None) if runtime_limits else None
    return RetryBudget(
        url_max_attempts=(url_retries + 1) if url_retries is not None else default,
        node_max_attempts=default,
        domain_max_attempts=default,
        task_max_attempts=default,
    )
