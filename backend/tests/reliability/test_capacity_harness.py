"""M-16 scoped 测试：small synthetic capacity harness（无外部网络）。"""

from __future__ import annotations

from app.domain.models import ResourceLease
from app.reliability.admission import ResourceAdmission
from app.reliability.capacity import CapacityConfig
from app.reliability.harness import CapacitySmokeResult, run_synthetic_capacity


def test_synthetic_capacity_smoke_bounds(db, users) -> None:
    cap = CapacityConfig(
        global_active_tasks=4,
        per_user_active_tasks=2,
        pool_concurrency={"core": 4, "http": 4, "browser": 1, "llm_search": 2},
    )
    adm = ResourceAdmission(db, cap)
    result = run_synthetic_capacity(adm, n_jobs=12, user_ids=[u.id for u in users])
    assert isinstance(result, CapacitySmokeResult)
    assert result.max_active <= cap.global_active_tasks
    assert result.leaked_leases == 0
    assert result.jobs_submitted == 12
    assert result.waiting_count >= 0


def test_no_leaked_lease_after_all_released(db, users) -> None:
    cap = CapacityConfig(global_active_tasks=3, per_user_active_tasks=2)
    adm = ResourceAdmission(db, cap)
    granted = []
    for i in range(6):
        slot = adm.try_acquire_task_slot(user_id=users[0].id, holder_id=f"j{i}")
        if slot.granted:
            granted.append(i)
    assert len(granted) == 2  # per-user=2
    for i in granted:
        adm.release_task_slot(user_id=users[0].id, holder_id=f"j{i}")
    assert db.query(ResourceLease).filter(ResourceLease.state == "active").count() == 0
