"""M-16 scoped 测试：域名 Circuit Breaker（TEST 2）。

CLOSED → failures → OPEN → 抑制请求 → cooldown → HALF_OPEN → 单探针 →
成功 CLOSED / 失败再 OPEN。验证 404/robots/凭据类错误不计入 Domain 崩溃，
且 OPEN 文案不泄漏其他用户数据。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.reliability.breaker import (
    CircuitBreakerRepository,
    CircuitBreakerService,
    CircuitBreakerState,
    normalize_domain,
)
from app.reliability.capacity import CapacityConfig
from app.reliability.errors import ErrorClass


def test_normalize_domain_strips_scheme_port_path() -> None:
    assert normalize_domain("https://www.example.com:8443/a/b?x=1") == "www.example.com"


def _make_service(db, *, threshold: int = 3, cooldown: int = 10) -> CircuitBreakerService:
    clock: dict[str, datetime] = {"now": datetime.now(UTC)}

    def _now() -> datetime:
        return clock["now"]

    service = CircuitBreakerService(
        repo=CircuitBreakerRepository(db),
        capacity=CapacityConfig(
            domain_breaker_threshold=threshold, domain_breaker_cooldown_seconds=cooldown
        ),
        now=_now,
    )
    service.clock = clock  # type: ignore[attr-defined]
    return service


def test_open_after_consecutive_domain_failures(db) -> None:
    b = _make_service(db, threshold=3)
    domain = "broken.test"
    for _ in range(3):
        b.record_failure(domain, ErrorClass.NETWORK_TIMEOUT, "connect timeout")
    assert b.state(domain) is CircuitBreakerState.OPEN
    allowed, msg = b.allow_request(domain)
    assert allowed is False
    assert "暂停" in (msg or "")


def test_open_does_not_leak_counts_or_other_user_data(db) -> None:
    b = _make_service(db, threshold=2)
    b.record_failure("x.test", ErrorClass.NETWORK_TIMEOUT, "boom")
    b.record_failure("x.test", ErrorClass.NETWORK_TIMEOUT, "boom")
    _, msg = b.allow_request("x.test")
    assert msg is not None
    assert "failed" not in msg.lower()
    assert "task" not in msg.lower()


def test_404_and_robots_do_not_trip_breaker(db) -> None:
    b = _make_service(db, threshold=2)
    b.record_failure("ok.test", ErrorClass.NON_RETRYABLE, "not found")
    b.record_failure("ok.test", ErrorClass.AUTH_FAILED, "credential invalid")
    assert b.state("ok.test") is CircuitBreakerState.CLOSED
    allowed, _ = b.allow_request("ok.test")
    assert allowed is True


def test_half_open_probe_recovers_after_success(db) -> None:
    b = _make_service(db, threshold=2, cooldown=60)
    domain = "probe.test"
    b.record_failure(domain, ErrorClass.NETWORK_TIMEOUT, "t")
    b.record_failure(domain, ErrorClass.NETWORK_TIMEOUT, "t")
    assert b.state(domain) is CircuitBreakerState.OPEN
    # 冷却期后进入 HALF_OPEN，允许单探针
    b.clock["now"] = b.clock["now"] + timedelta(seconds=61)  # type: ignore[attr-defined]
    assert b.state(domain) is CircuitBreakerState.HALF_OPEN
    allowed, _ = b.allow_request(domain)
    assert allowed is True
    b.record_success(domain)
    assert b.state(domain) is CircuitBreakerState.CLOSED


def test_half_open_probe_failure_reopens(db) -> None:
    b = _make_service(db, threshold=2, cooldown=60)
    domain = "reopen.test"
    b.record_failure(domain, ErrorClass.NETWORK_TIMEOUT, "t")
    b.record_failure(domain, ErrorClass.NETWORK_TIMEOUT, "t")
    b.clock["now"] = b.clock["now"] + timedelta(seconds=61)  # type: ignore[attr-defined]
    assert b.state(domain) is CircuitBreakerState.HALF_OPEN  # reconcile → HALF_OPEN
    # HALF_OPEN 单探针失败 → 立即重新 OPEN
    b.record_failure(domain, ErrorClass.TRANSIENT_SERVICE_ERROR, "probe 503")
    assert b.state(domain) is CircuitBreakerState.OPEN


def test_success_resets_consecutive_failures(db) -> None:
    b = _make_service(db, threshold=3)
    domain = "flaky.test"
    b.record_failure(domain, ErrorClass.NETWORK_TIMEOUT, "t")
    b.record_success(domain)
    b.record_failure(domain, ErrorClass.NETWORK_TIMEOUT, "t")
    assert b.state(domain) is CircuitBreakerState.CLOSED  # 未达阈值
