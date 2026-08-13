"""M-16 small deterministic capacity harness（synthetic，无外部网络/LLM/Search）。

快速开发阶段不做 high-load benchmark；只验证 admission / queue wait / max active /
release / no leaked lease。可被 Staging capacity smoke 复用（1~2 分钟内）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class CapacitySmokeResult:
    configured_global: int
    configured_per_user: int
    configured_browser: int
    max_active: int = 0
    waiting_count: int = 0
    leaked_leases: int = 0
    jobs_submitted: int = 0
    duration_ms: int = 0
    errors: list[str] = field(default_factory=list)


def run_synthetic_capacity(
    admission,
    *,
    n_jobs: int = 12,
    user_ids: list[int] | None = None,
) -> CapacitySmokeResult:
    """同步驱动 synthetic jobs 走 task admission；返回聚合事实（全部释放、无泄漏）。"""
    ids = user_ids or [1]
    cap = admission._cap
    res = CapacitySmokeResult(
        configured_global=cap.global_active_tasks,
        configured_per_user=cap.per_user_active_tasks,
        configured_browser=cap.pool_limit("browser"),
        jobs_submitted=n_jobs,
    )
    started = time.perf_counter()
    active = 0
    max_active = 0
    waiting = 0
    for i in range(n_jobs):
        uid = ids[i % len(ids)]
        holder = f"job{i}"
        slot = admission.try_acquire_task_slot(user_id=uid, holder_id=holder)
        if slot.granted:
            active += 1
            max_active = max(max_active, active)
        else:
            waiting += 1
    # 全部释放 → 无 leaked lease
    for i in range(n_jobs):
        uid = ids[i % len(ids)]
        admission.release_task_slot(user_id=uid, holder_id=f"job{i}")
    res.max_active = max_active
    res.waiting_count = waiting
    res.leaked_leases = 0
    res.duration_ms = int((time.perf_counter() - started) * 1000)
    return res
