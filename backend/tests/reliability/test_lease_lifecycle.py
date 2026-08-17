"""M-16 Resource Lease 生命周期修复 scoped 测试。

覆盖根因修复：
- C1：过期 lease 不再占有效容量（reaper 未跑也能自愈）。
- release 幂等（重复释放 / release 后 reap 均安全）。
- reaper 不动 fresh lease / 有界批次 / 幂等并发。
- pool 满 → 一个 lease 过期 → 容量自动恢复（无需手工清理）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.models import ResourceLease
from app.reliability.admission import ResourceAdmission
from app.reliability.capacity import CapacityConfig


def _expire(db, holder_id: str) -> None:
    db.query(ResourceLease).filter(ResourceLease.holder_id == holder_id).update(
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    db.commit()


def _insert_expired(db, holder_id: str, *, scope_key: str = "core") -> None:
    now = datetime.now(UTC)
    db.add(
        ResourceLease(
            scope="resource_class",
            scope_key=scope_key,
            holder_type="node",
            holder_id=holder_id,
            user_id=None,
            resource_class=scope_key,
            state="active",
            acquired_at=now,
            expires_at=now - timedelta(seconds=10),
            last_heartbeat_at=now,
        )
    )
    db.commit()


def _active_stale(db, prefix: str) -> int:
    return (
        db.query(ResourceLease)
        .filter(ResourceLease.holder_id.like(f"{prefix}-%"), ResourceLease.state == "active")
        .count()
    )


def test_expired_lease_does_not_consume_capacity(db, users) -> None:
    """C1：holder 崩溃后 lease 过期，即使 reaper 未运行，槽位也立即释放。"""
    cap = CapacityConfig(lease_ttl_seconds=30)
    adm = ResourceAdmission(db, cap)
    assert adm.try_acquire_pool_slot(resource_class="browser", holder_id="holder-a").granted
    assert not adm.try_acquire_pool_slot(resource_class="browser", holder_id="holder-b").granted

    _expire(db, "holder-a")
    # 过期 lease 不再计入容量 → 无需 reap 即可 acquire（reaper 缺失也不永久耗尽 pool）。
    assert adm.try_acquire_pool_slot(resource_class="browser", holder_id="holder-b").granted


def test_capacity_recovers_without_manual_cleanup(db, users) -> None:
    """pool 满（真实 active）→ 一个过期 → 容量自动可用（无需手工 DB 清理）。"""
    cap = CapacityConfig(lease_ttl_seconds=30, pool_concurrency={"browser": 1})
    adm = ResourceAdmission(db, cap)
    assert adm.try_acquire_pool_slot(resource_class="browser", holder_id="a").granted
    assert not adm.try_acquire_pool_slot(resource_class="browser", holder_id="b").granted

    _expire(db, "a")
    assert adm.try_acquire_pool_slot(resource_class="browser", holder_id="b").granted


def test_release_is_idempotent(db, users) -> None:
    """release 两次 / release 后 reap 均安全 no-op，不报致命错误。"""
    cap = CapacityConfig(lease_ttl_seconds=30)
    adm = ResourceAdmission(db, cap)
    adm.try_acquire_pool_slot(resource_class="http", holder_id="h1")
    adm.release_pool_slot(resource_class="http", holder_id="h1")
    # 重复释放安全（rowcount=0，无异常）
    adm.release_pool_slot(resource_class="http", holder_id="h1")
    # release 后 reap 不误改已释放行
    adm.reap()
    row = db.query(ResourceLease).filter(ResourceLease.holder_id == "h1").one()
    assert row.state == "released"


def test_reaper_ignores_fresh_leases(db, users) -> None:
    """未过期 lease 不被 reap（active 且 expires_at 未来）。"""
    cap = CapacityConfig(lease_ttl_seconds=60)
    adm = ResourceAdmission(db, cap)
    adm.try_acquire_pool_slot(resource_class="core", holder_id="fresh")
    assert adm.reap() == 0
    row = db.query(ResourceLease).filter(ResourceLease.holder_id == "fresh").one()
    assert row.state == "active"


def test_reaper_is_bounded(db, users) -> None:
    """reap 按批回收：limit=2 只回收 2，剩余仍 active；sweep 清空剩余。"""
    cap = CapacityConfig(lease_ttl_seconds=30)
    adm = ResourceAdmission(db, cap)
    for i in range(3):
        _insert_expired(db, f"stale-{i}")

    assert adm.reap(limit=2) == 2
    assert _active_stale(db, "stale") == 1
    assert adm.sweep(limit=2) == 1
    assert _active_stale(db, "stale") == 0


def test_reaper_is_idempotent(db, users) -> None:
    """reap 两次安全：第一次回收，第二次无副作用（幂等）。"""
    cap = CapacityConfig(lease_ttl_seconds=30)
    adm = ResourceAdmission(db, cap)
    _insert_expired(db, "stale-once")
    assert adm.reap() == 1
    assert adm.reap() == 0
