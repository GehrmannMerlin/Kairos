"""真实 plan-driven 执行单元（M-08）。

fetch_next_execution_unit：读取 run 对应 PlanVersion 的 graph，按拓扑顺序返回下一个
READY 单元（依赖已满足）；无更多单元返回 None。execute_safe_unit：dispatch 到
NODE_EXECUTORS（生产为空 → NODE_EXECUTOR_UNAVAILABLE）；测试/Staging fixture 注册
真实 NodeDefinition 的 fixture executor。
"""

from __future__ import annotations

from temporalio import activity

from app.activities.execution_seam import (
    ExecuteUnitInput,
    ExecuteUnitResult,
    ExecutionUnit,
    FetchUnitInput,
    FetchUnitResult,
)
from app.infra.deps import get_session_factory
from app.plan.executors import get_node_executor

# 允许的 NodeType 注册名（避免字符串枚举值漂移）
_NODE_TYPE_NAMES = {
    "source_search",
    "access_rules_check",
    "link_discovery",
    "fetch",
    "browser_render",
    "extract",
    "normalize",
    "deduplicate",
    "validate",
    "generate_artifact",
}


@activity.defn
async def fetch_next_execution_unit(inp: FetchUnitInput) -> FetchUnitResult:
    session = get_session_factory()()
    try:
        from app.domain.models import Run
        from app.domain.repository import PlanVersionRepository

        run = session.get(Run, inp.run_id)
        if run is None:
            return FetchUnitResult(unit=None)
        plan = PlanVersionRepository(session).get_version(
            run.user_id, run.task_id, run.plan_version
        )
        graph = ((plan.payload or {}).get("graph")) or {}
        nodes = graph.get("nodes", [])
        if inp.after_index >= len(nodes):
            return FetchUnitResult(unit=None)
        node = nodes[inp.after_index]
        node_type = str(node.get("node_type", ""))
        if node_type not in _NODE_TYPE_NAMES:
            return FetchUnitResult(unit=None)
        risk = (graph.get("node_risk_levels") or {}).get(str(node.get("node_id")), "low")
        parameters = node.get("parameters") or {}
        requires_approval = risk == "high"
        return FetchUnitResult(
            unit=ExecutionUnit(
                run_id=inp.run_id,
                index=inp.after_index + 1,
                unit_type=node_type,
                input_fingerprint=str(
                    node.get("input_fingerprint") or f"fp-{inp.run_id}-{inp.after_index + 1}"
                ),
                node_id=node.get("node_id"),
                node_type=node_type,
                definition_version=node.get("definition_version"),
                parameters=parameters,
                requires_approval=requires_approval,
                approval_action_type=f"{node_type}_non_public" if requires_approval else None,
                approval_target=str(parameters.get("url_template") or ""),
                approval_parameters=parameters,
                credential_ref=parameters.get("credential_ref"),
            )
        )
    finally:
        session.close()


@activity.defn
async def execute_safe_unit(inp: ExecuteUnitInput) -> ExecuteUnitResult:
    executor = get_node_executor(inp.unit.node_type)
    if executor is None:
        return ExecuteUnitResult(
            unit_index=inp.unit.index,
            committed_refs={},
            status="NODE_EXECUTOR_UNAVAILABLE",
            error_code="NODE_EXECUTOR_UNAVAILABLE",
        )
    return await executor(inp.unit)
