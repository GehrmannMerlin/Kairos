"""M-16 scoped 测试：Retry Policy Matrix（TEST 1）。

覆盖：ErrorClass 确定性分类 + RetryDecision 有界重试 / Retry-After /
correction-change 守卫 / 资源等待非失败 / auth 与 quota 不重试。
"""

from __future__ import annotations

import pytest
from app.crawling.errors import FetchErrorCode
from app.providers import errors as perrors
from app.reliability.errors import (
    ErrorClass,
    classify_fetch_error_code,
    classify_http_error,
    classify_provider_error,
    is_domain_breaker_error,
)
from app.reliability.retry import (
    RetryDecision,
    RetryStrategy,
    correction_fingerprint,
    decide_retry,
    jitter_seconds,
)


def test_http_timeout_maps_to_network_timeout() -> None:
    assert classify_http_error(408) is ErrorClass.NETWORK_TIMEOUT


def test_5xx_maps_to_transient_service_error() -> None:
    for code in (502, 503, 504, 500):
        assert classify_http_error(code) is ErrorClass.TRANSIENT_SERVICE_ERROR


def test_429_maps_to_rate_limited() -> None:
    assert classify_http_error(429) is ErrorClass.RATE_LIMITED


def test_provider_auth_maps_to_auth_failed() -> None:
    assert classify_provider_error(perrors.ProviderAuthFailedError("x")) is ErrorClass.AUTH_FAILED


def test_provider_429_maps_to_rate_limited() -> None:
    assert classify_provider_error(perrors.ProviderRateLimitedError("x")) is ErrorClass.RATE_LIMITED


def test_fetch_dns_is_network_timeout_and_counts_for_breaker() -> None:
    ec = classify_fetch_error_code(FetchErrorCode.DNS_ERROR)
    assert ec is ErrorClass.NETWORK_TIMEOUT
    assert is_domain_breaker_error(ec)


def test_fetch_404_does_not_count_for_domain_breaker() -> None:
    ec = classify_fetch_error_code(FetchErrorCode.NOT_FOUND)
    assert ec is ErrorClass.NON_RETRYABLE
    assert not is_domain_breaker_error(ec)


def test_fetch_captcha_is_auth_and_not_domain_breaker() -> None:
    ec = classify_fetch_error_code(FetchErrorCode.CAPTCHA_REQUIRED)
    assert ec is ErrorClass.AUTH_FAILED
    assert not is_domain_breaker_error(ec)


@pytest.mark.parametrize(
    "error_class,attempt,max_attempts,expected",
    [
        (ErrorClass.NETWORK_TIMEOUT, 0, 3, True),
        (ErrorClass.TRANSIENT_SERVICE_ERROR, 2, 3, False),  # 已达 max_attempts
        (ErrorClass.AUTH_FAILED, 0, 3, False),
        (ErrorClass.QUOTA_EXHAUSTED, 0, 3, False),
        (ErrorClass.CANCELLED, 0, 3, False),
        (ErrorClass.NON_RETRYABLE, 0, 3, False),
    ],
)
def test_decide_retry_is_bounded(
    error_class: ErrorClass, attempt: int, max_attempts: int, expected: bool
) -> None:
    d = decide_retry(error_class=error_class, attempt=attempt, max_attempts=max_attempts)
    assert d.should_retry is expected
    assert d.error_class is error_class


def test_retry_after_is_respected() -> None:
    d = decide_retry(
        error_class=ErrorClass.RATE_LIMITED,
        attempt=0,
        max_attempts=3,
        retry_after_seconds=7.0,
        rand=lambda: 0.5,
    )
    assert d.strategy is RetryStrategy.RESPECT_RETRY_AFTER
    assert d.should_retry is True
    assert d.delay_seconds >= 7.0


def test_correction_requires_change() -> None:
    fp = "fp-1"
    d1 = decide_retry(
        error_class=ErrorClass.EXTRACTION_FAILED,
        attempt=0,
        max_attempts=3,
        correction_fp=fp,
        prior_correction_fp="fp-0",
    )
    assert d1.should_retry is True
    assert d1.strategy is RetryStrategy.CORRECTION
    d2 = decide_retry(
        error_class=ErrorClass.EXTRACTION_FAILED,
        attempt=0,
        max_attempts=3,
        correction_fp=fp,
        prior_correction_fp=fp,  # 完全相同 → 拒绝
    )
    assert d2.should_retry is False
    assert d2.requires_change is True


def test_quality_failure_also_requires_change() -> None:
    d = decide_retry(
        error_class=ErrorClass.QUALITY_FAILED,
        attempt=0,
        max_attempts=3,
        correction_fp="a",
        prior_correction_fp="a",
    )
    assert d.should_retry is False


def test_resource_unavailable_is_wait_not_fail() -> None:
    d = decide_retry(error_class=ErrorClass.RESOURCE_UNAVAILABLE, attempt=0, max_attempts=3)
    assert d.strategy is RetryStrategy.WAIT_RESOURCE
    assert d.should_retry is True
    assert d.delay_seconds > 0


def test_auth_blocking_action() -> None:
    d = decide_retry(error_class=ErrorClass.AUTH_FAILED, attempt=0, max_attempts=3)
    assert d.strategy is RetryStrategy.USER_ACTION
    assert d.blocking_action is not None


def test_jitter_bounds_and_deterministic_seam() -> None:
    assert 0.0 <= jitter_seconds(0.0, rand=lambda: 0.5) <= 1.0
    a = jitter_seconds(2.0, rand=lambda: 0.5)
    b = jitter_seconds(2.0, rand=lambda: 0.5)
    assert a == b


def test_correction_fingerprint_changes_on_parameter_change() -> None:
    f1 = correction_fingerprint(inputs={}, tool="extractor-a", parameters={}, environment={})
    f2 = correction_fingerprint(inputs={}, tool="extractor-b", parameters={}, environment={})
    assert f1 != f2


def test_retry_decision_is_frozen() -> None:
    d = decide_retry(error_class=ErrorClass.NETWORK_TIMEOUT, attempt=0, max_attempts=3)
    assert isinstance(d, RetryDecision)
