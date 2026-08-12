"""M-16 scoped 测试：Global + Per-User Admission（TEST 3）。

global=3, per-user=2：User A 提交 3 个 synthetic jobs、User B 提交 1 个。
证明 A 最多同时 2、B 可运行、总 active <= 3；多余 A 等待（非失败），
A 释放后可获得。
"""

from __future__ import annotations

from app.domain.models import ResourceLease
from app.reliability.admission import LeaseScope, ResourceAdmission, SlotResult
from app.reliability.capacity import CapacityConfig


def _active(db) -> int:
    return db.query(ResourceLease).filter(
        ResourceLease.scope == LeaseScope.GLOBAL.value,
        ResourceLease.state == "active",
    ).count()


def test_global_and_user_admission_are_fair(db, users) -> None:
    user_a, user_b = users
    cap = CapacityConfig(global_active_tasks=3, per_user_active_tasks=2)
    adm = ResourceAdmission(db, cap)

    a1 = adm.try_acquire_task_slot(user_id=user_a.id, holder_id="a1")
    a2 = adm.try_acquire_task_slot(user_id=user_a.id, holder_id="a2")
    assert a1.granted and a2.granted

    a3 = adm.try_acquire_task_slot(user_id=user_a.id, holder_id="a3")
    assert not a3.granted
    assert a3.reason == "per_user_limit"  # A 超限 → 等待不是失败

    b1 = adm.try_acquire_task_slot(user_id=user_b.id, holder_id="b1")
    assert b1.granted  # B 仍有公平机会

    assert _active(db) <= 3  # 总 active 不超 global

    # A 释放一个 → A 的第三个可获得
    adm.release_task_slot(user_id=user_a.id, holder_id="a1")
    a3b = adm.try_acquire_task_slot(user_id=user_a.id, holder_id="a3")
    assert a3b.granted


def test_global_limit_bounds_total(db, users) -> None:
    user_a, user_b = users
    cap = CapacityConfig(global_active_tasks=2, per_user_active_tasks=1)
    adm = ResourceAdmission(db, cap)
    assert adm.try_acquire_task_slot(user_id=user_a.id, holder_id="a1").granted
    r2 = adm.try_acquire_task_slot(user_id=user_b.id, holder_id="b1")
    assert r2.granted
    r3 = adm.try_acquire_task_slot(user_id=user_a.id, holder_id="a2")
    assert not r3.granted  # 全局已满（即使 A 的 per-user 也满，先 global）
    assert _active(db) == 2


def test_slot_result_shape(db, users) -> None:
    user_a, _ = users
    cap = CapacityConfig()
    adm = ResourceAdmission(db, cap)
    r = adm.try_acquire_task_slot(user_id=user_a.id, holder_id="shape")
    assert isinstance(r, SlotResult)
    assert r.retry_after_seconds > 0
