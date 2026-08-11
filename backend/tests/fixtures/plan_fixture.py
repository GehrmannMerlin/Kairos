"""M-08 Staging/Test fixture executor（fixture-only，非 Production）。

使用真实标准 NodeDefinition；executor 只在测试/Staging plan_fixture_mode 下注册。
无真实外部网络副作用，无真实第三方写入，无真实凭据外传（dummy 测试引用）。

Gate-2 模拟场景：标准 FETCH Node + NON_PUBLIC/CREDENTIAL_ACCESS 高风险参数 → Validator
判为 HIGH_RISK → Approval PENDING → 用户批准 → fixture executor 完成 → Workflow 继续。
"""

from __future__ import annotations

import asyncio

from app.activities.execution_seam import ExecuteUnitResult, ExecutionUnit
from app.plan.executors import register_node_executor
from app.plan.nodes import NodeType


async def _fixture_fetch(unit: ExecutionUnit) -> ExecuteUnitResult:
    """模拟抓取：短小安全单元，保证命令有窗口触发；无真实网络请求。"""
    await asyncio.sleep(0.05)
    return ExecuteUnitResult(
        unit_index=unit.index,
        status="OK",
        committed_refs={
            "run_id": unit.run_id,
            "unit": unit.index,
            "node_id": unit.node_id,
            "node_type": unit.node_type,
            # dummy 测试凭据引用：Secret 是测试值，不得外传/写入真实 Credential
            "credential_ref": unit.credential_ref or "dummy:test-credential",
        },
    )


def install_fixture_executors() -> None:
    """仅测试/Staging worker 调用；Production 不调用。"""
    register_node_executor(NodeType.FETCH, _fixture_fetch)
