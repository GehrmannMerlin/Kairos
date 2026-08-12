"""M-16 域名 Circuit Breaker（CLOSED / OPEN / HALF_OPEN）。

部署级：保护目标域名，无 owner。只统计 is_domain_breaker_error 类错误
（DNS/connect timeout/连续 5xx/network unavailable）；robots denied、404、
用户凭据 401、Provider Key 错误不计入。OPEN 抑制请求；冷却后 HALF_OPEN
允许单探针（条件 UPDATE 原子认领），成功 → CLOSED，失败 → 重新 OPEN。
UI 只见脱敏文案（_SAFE_MESSAGE），不泄漏其他用户数据。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from urllib.parse import urlparse

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.models import DomainCircuitBreaker
from app.reliability.capacity import CapacityConfig
from app.reliability.errors import ErrorClass, is_domain_breaker_error

_SAFE_MESSAGE = "目标站点暂时不可用，系统已暂停请求"


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite 读回 timezone 列是 naive；统一视为 UTC，避免 aware/naive 比较失败。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class CircuitBreakerState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


def normalize_domain(url_or_host: str) -> str:
    host = url_or_host.strip().lower()
    if "://" in host or host.startswith("/"):
        host = urlparse(host if "://" in host else f"//{host}").hostname or host
    host = re.sub(r":\d+$", "", host)  # 去端口
    return host.strip(".")


class CircuitBreakerRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, domain: str) -> DomainCircuitBreaker | None:
        return self._db.scalars(
            select(DomainCircuitBreaker).where(DomainCircuitBreaker.domain == domain)
        ).first()

    def _upsert(self, domain: str) -> DomainCircuitBreaker:
        row = self.get(domain)
        if row is None:
            row = DomainCircuitBreaker(
                domain=domain,
                state=CircuitBreakerState.CLOSED,
                updated_at=datetime.now(UTC),
            )
            self._db.add(row)
            self._db.flush()
        return row


class CircuitBreakerService:
    def __init__(
        self,
        repo: CircuitBreakerRepository,
        capacity: CapacityConfig,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repo
        self._threshold = capacity.domain_breaker_threshold
        self._cooldown = timedelta(seconds=capacity.domain_breaker_cooldown_seconds)
        self._now = now or (lambda: datetime.now(UTC))

    def state(self, domain: str) -> CircuitBreakerState:
        row = self._repo.get(normalize_domain(domain))
        if row is None:
            return CircuitBreakerState.CLOSED
        self._reconcile(row)
        return CircuitBreakerState(row.state)

    def allow_request(self, domain: str) -> tuple[bool, str | None]:
        """是否允许向目标域名发请求。False → 附脱敏文案（无失败计数/无其他用户信息）。"""
        dom = normalize_domain(domain)
        row = self._repo.get(dom)
        if row is None:
            return True, None
        self._reconcile(row)
        if row.state == CircuitBreakerState.OPEN:
            return False, _SAFE_MESSAGE
        if row.state == CircuitBreakerState.HALF_OPEN and not self._claim_probe(row):
            return False, _SAFE_MESSAGE
        return True, None

    def record_success(self, domain: str) -> None:
        row = self._repo._upsert(normalize_domain(domain))
        row.consecutive_failures = 0
        row.state = CircuitBreakerState.CLOSED
        row.open_until = None
        row.half_open_at = None
        row.half_open_probe_claimed = False
        row.updated_at = self._now()
        self._repo._db.commit()

    def record_failure(self, domain: str, error_class: ErrorClass, message: str) -> None:
        dom = normalize_domain(domain)
        if not is_domain_breaker_error(error_class):
            return  # robots/404/凭据类错误不计入 Domain 崩溃（D-013 §20）
        row = self._repo._upsert(dom)
        row.consecutive_failures += 1
        row.failure_count += 1
        row.last_error_class = error_class.value
        row.updated_at = self._now()
        if row.state == CircuitBreakerState.HALF_OPEN:
            # HALF_OPEN 单探针失败 → 立即重新 OPEN
            row.state = CircuitBreakerState.OPEN
            row.open_until = self._now() + self._cooldown
            row.open_reason = _SAFE_MESSAGE
            row.half_open_probe_claimed = False
        elif row.state != CircuitBreakerState.OPEN and row.consecutive_failures >= self._threshold:
            row.state = CircuitBreakerState.OPEN
            row.open_until = self._now() + self._cooldown
            row.open_reason = _SAFE_MESSAGE
        self._repo._db.commit()

    # ---- 内部 ----

    def _reconcile(self, row: DomainCircuitBreaker) -> None:
        now = self._now()
        open_until = _as_utc(row.open_until)
        if row.state == CircuitBreakerState.OPEN and open_until and now >= open_until:
            row.state = CircuitBreakerState.HALF_OPEN
            row.half_open_at = now
            row.half_open_probe_claimed = False
            row.updated_at = now
            self._repo._db.commit()

    def _claim_probe(self, row: DomainCircuitBreaker) -> bool:
        """单探针原子认领：HALF_OPEN 下只放行一次（跨 worker 安全）。"""
        res = self._repo._db.execute(
            update(DomainCircuitBreaker)
            .where(
                DomainCircuitBreaker.id == row.id,
                DomainCircuitBreaker.state == CircuitBreakerState.HALF_OPEN,
                DomainCircuitBreaker.half_open_probe_claimed.is_(False),
            )
            .values(half_open_probe_claimed=True, updated_at=self._now())
        )
        self._repo._db.commit()
        return bool(getattr(res, "rowcount", 0))
