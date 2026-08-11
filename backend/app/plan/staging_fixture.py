"""Staging Gate fixture harness（M-08 / DEPLOY-GATE-2 / §48）。

仅当 ``KAIROS_PLAN_FIXTURE_MODE=true`` 时由 worker 调用 ``install_staging_fixture()``。
使用真实标准 NodeDefinition；executor 为 fixture-only，无真实外部网络副作用、无真实
第三方写入、无真实凭据外传（dummy 测试引用）。Production 环境默认关闭且强制关闭。

Gate-2 场景：标准 FETCH Node + NON_PUBLIC/CREDENTIAL_ACCESS 高风险参数 → Validator
判为 HIGH_RISK → 真实 Approval PENDING → 用户批准 → fixture executor 完成 → Workflow
继续。绝不注册 TEST_NODE 到 Production Registry。
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
            "credential_ref": unit.credential_ref or "dummy:staging-test",
        },
    )


def install_staging_fixture() -> None:
    """仅 Staging/测试 worker 调用；Production 不调用（plan_fixture_mode=False）。"""
    register_node_executor(NodeType.FETCH, _fixture_fetch)
