"""M-16 三级 ResourceAdmission（D-071）+ PostgreSQL-backed Resource Lease。

Level 1 GLOBAL / Level 2 USER → task slot（acquire 于 ensure_run_started，release 于终态）。
Level 3 RESOURCE_CLASS → pool slot（acquire 于 execute_safe_unit 前，finally 释放）。
acquire 的「count < limit 再 insert」在 PostgreSQL 用 pg_advisory_xact_lock 保证
跨进程原子；SQLite（测试）单写者直接 count。lease heartbeat 只是资源占用事实，
不是业务 Checkpoint（M-04/M-07 不变）。reaper 按 TTL 回收异常退出 worker 的 slot。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.models import ResourceLease


class LeaseScope(StrEnum):
    GLOBAL = "global"
    USER = "user"
    RESOURCE_CLASS = "resource_class"


@dataclass(frozen=True)
class SlotResult:
    granted: bool
    reason: str | None = None  # global_limit | per_user_limit | pool_limit | None
    retry_after_seconds: float = 5.0


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite 读回 timezone 列是 naive；统一视为 UTC，避免 aware/naive 比较失败。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class ResourceLeaseRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def count_active(self, scope: str, scope_key: str) -> int:
        return int(
            self._db.scalar(
                select(func.count())
                .select_from(ResourceLease)
                .where(
                    ResourceLease.scope == scope,
                    ResourceLease.scope_key == scope_key,
                    ResourceLease.state == "active",
                )
            )
            or 0
        )

    def acquire(
        self,
        *,
        scope: str,
        scope_key: str,
        holder_type: str,
        holder_id: str,
        limit: int,
        ttl_seconds: int,
        user_id: int | None,
        resource_class: str | None,
        now: datetime,
    ) -> bool:
        self._pg_advisory_lock(f"{scope}:{scope_key}")
        if self.count_active(scope, scope_key) >= limit:
            return False
        lease = ResourceLease(
            scope=scope,
            scope_key=scope_key,
            holder_type=holder_type,
            holder_id=holder_id,
            user_id=user_id,
            resource_class=resource_class,
            state="active",
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            last_heartbeat_at=now,
        )
        self._db.add(lease)
        self._db.commit()
        return True

    def release(self, *, scope: str, scope_key: str, holder_id: str, now: datetime) -> bool:
        res = self._db.execute(
            update(ResourceLease)
            .where(
                ResourceLease.scope == scope,
                ResourceLease.scope_key == scope_key,
                ResourceLease.holder_id == holder_id,
                ResourceLease.state == "active",
            )
            .values(state="released", released_at=now)
        )
        self._db.commit()
        return bool(getattr(res, "rowcount", 0))

    def heartbeat(
        self, *, scope: str, scope_key: str, holder_id: str, ttl_seconds: int, now: datetime
    ) -> bool:
        res = self._db.execute(
            update(ResourceLease)
            .where(
                ResourceLease.scope == scope,
                ResourceLease.scope_key == scope_key,
                ResourceLease.holder_id == holder_id,
                ResourceLease.state == "active",
            )
            .values(expires_at=now + timedelta(seconds=ttl_seconds), last_heartbeat_at=now)
        )
        self._db.commit()
        return bool(getattr(res, "rowcount", 0))

    def reap_expired(self, now: datetime) -> int:
        res = self._db.execute(
            update(ResourceLease)
            .where(ResourceLease.state == "active", ResourceLease.expires_at < now)
            .values(state="expired", released_at=now)
        )
        self._db.commit()
        return int(getattr(res, "rowcount", 0) or 0)

    def _pg_advisory_lock(self, key: str) -> None:
        if self._db.bind is not None and self._db.bind.dialect.name == "postgresql":
            lock_id = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:15], 16)
            self._db.execute(select(func.pg_advisory_xact_lock(lock_id)))


class ResourceAdmission:
    def __init__(
        self, db: Session, capacity, *, now: Callable[[], datetime] | None = None
    ) -> None:
        self._db = db
        self._cap = capacity
        self._repo = ResourceLeaseRepository(db)
        self._now = now or (lambda: datetime.now(UTC))

    # ---- Level 1 + 2：task slot ----

    def try_acquire_task_slot(self, *, user_id: int, holder_id: str) -> SlotResult:
        now = self._now()
        if not self._repo.acquire(
            scope=LeaseScope.GLOBAL.value,
            scope_key="deploy",
            holder_type="run",
            holder_id=holder_id,
            limit=self._cap.global_active_tasks,
            ttl_seconds=self._cap.lease_ttl_seconds,
            user_id=user_id,
            resource_class=None,
            now=now,
        ):
            return SlotResult(False, reason="global_limit")
        if not self._repo.acquire(
            scope=LeaseScope.USER.value,
            scope_key=str(user_id),
            holder_type="run",
            holder_id=holder_id,
            limit=self._cap.per_user_active_tasks,
            ttl_seconds=self._cap.lease_ttl_seconds,
            user_id=user_id,
            resource_class=None,
            now=now,
        ):
            # 半获得：回滚 global slot
            self._repo.release(
                scope=LeaseScope.GLOBAL.value, scope_key="deploy", holder_id=holder_id, now=now
            )
            return SlotResult(False, reason="per_user_limit")
        return SlotResult(True)

    def release_task_slot(self, *, user_id: int, holder_id: str) -> None:
        now = self._now()
        self._repo.release(
            scope=LeaseScope.GLOBAL.value, scope_key="deploy", holder_id=holder_id, now=now
        )
        self._repo.release(
            scope=LeaseScope.USER.value, scope_key=str(user_id), holder_id=holder_id, now=now
        )

    def heartbeat_task_slot(self, *, user_id: int, holder_id: str) -> None:
        now = self._now()
        for scope, key in (
            (LeaseScope.GLOBAL.value, "deploy"),
            (LeaseScope.USER.value, str(user_id)),
        ):
            self._repo.heartbeat(
                scope=scope, scope_key=key, holder_id=holder_id,
                ttl_seconds=self._cap.lease_ttl_seconds, now=now,
            )

    # ---- Level 3：pool slot ----

    def try_acquire_pool_slot(
        self, *, resource_class: str, holder_id: str, user_id: int | None = None
    ) -> SlotResult:
        limit = self._cap.pool_limit(resource_class)
        now = self._now()
        if not self._repo.acquire(
            scope=LeaseScope.RESOURCE_CLASS.value,
            scope_key=resource_class,
            holder_type="node",
            holder_id=holder_id,
            limit=limit,
            ttl_seconds=self._cap.lease_ttl_seconds,
            user_id=user_id,
            resource_class=resource_class,
            now=now,
        ):
            return SlotResult(False, reason="pool_limit", retry_after_seconds=5.0)
        return SlotResult(True)

    def release_pool_slot(self, *, resource_class: str, holder_id: str) -> None:
        now = self._now()
        self._repo.release(
            scope=LeaseScope.RESOURCE_CLASS.value,
            scope_key=resource_class,
            holder_id=holder_id,
            now=now,
        )

    def heartbeat_pool_slot(self, *, resource_class: str, holder_id: str) -> None:
        now = self._now()
        self._repo.heartbeat(
            scope=LeaseScope.RESOURCE_CLASS.value,
            scope_key=resource_class,
            holder_id=holder_id,
            ttl_seconds=self._cap.lease_ttl_seconds,
            now=now,
        )

    def reap(self) -> int:
        return self._repo.reap_expired(self._now())


class LeaseReaper:
    """定时回收过期 lease（worker 异常退出后 slot 最终释放）。由 worker 后台任务周期调用。"""

    def __init__(self, admission: ResourceAdmission, interval_seconds: int) -> None:
        self._admission = admission
        self._interval = interval_seconds

    @property
    def interval_seconds(self) -> int:
        return self._interval

    async def run_once(self) -> int:
        return self._admission.reap()
