"""真实 plan-driven 执行单元（M-08）。

fetch_next_execution_unit：读取 run 对应 PlanVersion 的 graph，按拓扑顺序返回下一个
READY 单元（依赖已满足）；无更多单元返回 None。execute_safe_unit：dispatch 到
NODE_EXECUTORS（生产为空 → NODE_EXECUTOR_UNAVAILABLE）；测试/Staging fixture 注册
真实 NodeDefinition 的 fixture executor。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

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
logger = logging.getLogger(__name__)


async def _pool_slot_heartbeat_loop(capacity, resource_class: str, holder: str) -> None:
    """执行期间周期续期 pool slot lease（资源占用事实，非业务 Checkpoint）。

    用独立 session 与主 activity 的 admission_session 隔离（SQLAlchemy Session 非
    并发安全）。心跳间隔来自 CapacityConfig.lease_heartbeat_seconds；任务取消时由
    调用方 cancel 本 task，使 finally 先停心跳再释放 lease，避免过期误判。
    """
    from app.reliability.admission import ResourceAdmission

    interval = capacity.lease_heartbeat_seconds
    while True:
        await asyncio.sleep(interval)
        session = get_session_factory()()
        try:
            ResourceAdmission(session, capacity).heartbeat_pool_slot(
                resource_class=resource_class, holder_id=holder
            )
        except Exception:
            session.rollback()
            logger.warning(
                "pool_slot_heartbeat_failed resource_class=%s holder=%s",
                resource_class,
                holder,
                exc_info=True,
            )
        finally:
            session.close()


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
        # M-16：确定性 ResourceClass → TaskQueue 路由用（来自 NodeDefinition.resource_class）
        from app.plan.nodes import NodeRegistry

        rc = None
        try:
            definition = NodeRegistry().get(str(node_type))
            rc = definition.resource_class.value if definition else None
        except Exception:
            rc = None
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
                resource_class=rc,
            )
        )
    finally:
        session.close()


@activity.defn
async def execute_safe_unit(inp: ExecuteUnitInput) -> ExecuteUnitResult:
    # M-16 Level 3 pool admission（D-071）：资源类单元先占 pool slot；无 slot → RESOURCE_WAITING
    # （等待而非失败），finally 释放。CORE 单元（rc=None）不走 pool 准入。
    from app.config import get_settings
    from app.reliability.admission import ResourceAdmission
    from app.reliability.capacity import capacity_from_settings

    rc = inp.unit.resource_class
    holder = f"run{inp.run_id}-node{inp.unit.node_id or inp.unit.index}"
    admission_session = None
    heartbeat_task = None
    if rc is not None:
        admission_session = get_session_factory()()
        capacity = capacity_from_settings(get_settings())
        adm = ResourceAdmission(admission_session, capacity)
        slot = adm.try_acquire_pool_slot(resource_class=rc, holder_id=holder, user_id=None)
        if not slot.granted:
            admission_session.close()
            return ExecuteUnitResult(
                unit_index=inp.unit.index,
                committed_refs={
                    "waiting_reason": "pool_limit",
                    "resource_class": rc,
                    "wait_seconds": slot.retry_after_seconds,
                },
                status="RESOURCE_WAITING",
                error_code="RESOURCE_UNAVAILABLE",
            )
        # 执行期间续期 lease：长节点（如 extract）> TTL 时不因过期被误判可回收。
        heartbeat_task = asyncio.create_task(_pool_slot_heartbeat_loop(capacity, rc, holder))
    try:
        try:
            attempt = activity.info().attempt
        except RuntimeError:
            # Direct Activity unit tests have no Temporal context.
            attempt = 1
        from app.execution.lifecycle import ExecutionLifecycleRecorder

        lifecycle_session = get_session_factory()()
        lifecycle = ExecutionLifecycleRecorder(lifecycle_session)
        lifecycle.start_attempt(run_id=inp.run_id, unit=inp.unit, attempt=attempt)
        try:
            executor = get_node_executor(inp.unit.node_type)
            if executor is None:
                result = ExecuteUnitResult(
                    unit_index=inp.unit.index,
                    committed_refs={},
                    status="NODE_EXECUTOR_UNAVAILABLE",
                    error_code="NODE_EXECUTOR_UNAVAILABLE",
                )
            else:
                result = await executor(inp.unit)
        except Exception:
            # The executor error is the caller-visible failure. Lifecycle
            # persistence is best-effort here and must not mask it.
            try:
                lifecycle.finish_attempt(
                    run_id=inp.run_id,
                    unit=inp.unit,
                    attempt=attempt,
                    status="FAILED",
                    committed_refs={},
                    error_code="INTERNAL",
                )
            except Exception:
                lifecycle_session.rollback()
                logger.warning(
                    "lifecycle_finish_failed run_id=%s node_id=%s attempt=%s",
                    inp.run_id,
                    inp.unit.node_id or inp.unit.index,
                    attempt,
                )
            raise
        lifecycle_status = "SUCCEEDED" if result.status == "OK" else result.status
        lifecycle_error_code = result.error_code
        if lifecycle_status == "CREDENTIAL_REQUIRED":
            lifecycle_status = "WAITING_APPROVAL"
            lifecycle_error_code = result.error_code or "CREDENTIAL_REQUIRED"
        lifecycle.finish_attempt(
            run_id=inp.run_id,
            unit=inp.unit,
            attempt=attempt,
            status=lifecycle_status,
            committed_refs=result.committed_refs,
            error_code=lifecycle_error_code,
            safe_message=result.safe_message,
        )
        return result
    finally:
        if "lifecycle_session" in locals():
            lifecycle_session.close()
        # 先停心跳再释放 lease，避免释放后心跳又把已释放行改回 active（幂等条件已防，
        # 但顺序上先停更清晰，且确保 finally 内没有仍在飞的心跳事务）。
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        if rc is not None and admission_session is not None:
            try:
                ResourceAdmission(admission_session, capacity).release_pool_slot(
                    resource_class=rc, holder_id=holder
                )
            except Exception:
                admission_session.rollback()
            admission_session.close()
