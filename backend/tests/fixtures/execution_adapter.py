"""M-07 集成测试 fixture 执行单元。

只允许在测试 worker（fixture_worker.py / test worker）注册；绝不允许进入
app.worker 的 Production Worker（I-002 / M-07 边界）。每个单元：
  - execute：heartbeat 进度，返回 committed_refs（无持久副作用）
  - commit：由 TaskWorkflow 调 commit_checkpoint Activity 持久化
"""

from __future__ import annotations

import asyncio

from app.activities.execution_seam import (
    ExecuteUnitInput,
    ExecuteUnitResult,
    ExecutionUnit,
    FetchUnitInput,
    FetchUnitResult,
)
from app.activities.heartbeat import heartbeat_progress
from temporalio import activity

# 每个 run 返回固定 3 个安全单元，随后 None（模拟一小批执行计划）。
_FIXTURE_UNITS_PER_RUN = 3


@activity.defn
async def fetch_next_execution_unit(inp: FetchUnitInput) -> FetchUnitResult:
    index = inp.after_index + 1
    if index > _FIXTURE_UNITS_PER_RUN:
        return FetchUnitResult(unit=None)
    return FetchUnitResult(
        unit=ExecutionUnit(
            run_id=inp.run_id,
            index=index,
            unit_type="fixture_safe_unit",
            input_fingerprint=f"fp-{inp.run_id}-{index}",
        )
    )


@activity.defn
async def execute_safe_unit(inp: ExecuteUnitInput) -> ExecuteUnitResult:
    heartbeat_progress(done=inp.unit.index, total=_FIXTURE_UNITS_PER_RUN, note="fixture unit")
    await asyncio.sleep(0.05)  # 模拟短小安全单元
    return ExecuteUnitResult(
        unit_index=inp.unit.index,
        committed_refs={"run_id": inp.run_id, "unit": inp.unit.index},
    )
