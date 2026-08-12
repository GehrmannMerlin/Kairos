"""M-16 scoped 测试：Lease Recovery（TEST 6）+ heartbeat 延长。

Worker 异常退出（停止 heartbeat）→ TTL/reaper 回收 → waiting job 可 acquire。
heartbeat 只是资源占用事实，延长 lease 但绝不充当业务 Checkpoint。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.models import ResourceLease
from app.reliability.admission import ResourceAdmission
from app.reliability.capacity import CapacityConfig


def _expire_lease(db, holder_id: str) -> None:
    db.query(ResourceLease).filter(ResourceLease.holder_id == holder_id).update(
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    db.commit()


def test_expired_lease_is_reaped_and_reacquired(db, users) -> None:
    """holder 消失（停 heartbeat）→ TTL/reaper 回收 → waiting job 可 acquire。"""
    user_a, _ = users
    cap = CapacityConfig(lease_ttl_seconds=30, lease_heartbeat_seconds=5)
    adm = ResourceAdmission(db, cap)
    assert adm.try_acquire_pool_slot(resource_class="browser", holder_id="dead-holder").granted
    assert not adm.try_acquire_pool_slot(
        resource_class="browser", holder_id="waiter"
    ).granted

    # 模拟 holder 进程消失：lease 过期 → reaper 回收
    _expire_lease(db, "dead-holder")
    assert adm.reap() == 1
    assert adm.try_acquire_pool_slot(resource_class="browser", holder_id="waiter").granted


def test_heartbeat_extends_lease(db, users) -> None:
    user_a, _ = users
    cap = CapacityConfig(lease_ttl_seconds=30, lease_heartbeat_seconds=5)
    adm = ResourceAdmission(db, cap)
    adm.try_acquire_pool_slot(resource_class="browser", holder_id="alive")
    before = (
        db.query(ResourceLease).filter(ResourceLease.holder_id == "alive").one().expires_at
    )
    _expire_lease(db, "alive")
    adm.heartbeat_pool_slot(resource_class="browser", holder_id="alive")
    after = db.query(ResourceLease).filter(ResourceLease.holder_id == "alive").one().expires_at
    assert after > before


def test_release_marks_lease_released(db, users) -> None:
    user_a, _ = users
    cap = CapacityConfig(lease_ttl_seconds=30)
    adm = ResourceAdmission(db, cap)
    adm.try_acquire_pool_slot(resource_class="http", holder_id="h1")
    adm.release_pool_slot(resource_class="http", holder_id="h1")
    row = db.query(ResourceLease).filter(ResourceLease.holder_id == "h1").one()
    assert row.state == "released"
    # 释放后可立即重新 acquire（不会误判为占用）
    assert adm.try_acquire_pool_slot(resource_class="http", holder_id="h2").granted
