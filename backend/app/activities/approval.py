"""Approval Activities — Workflow 内 JIT 审批 + 拒绝后的合法 block（M-08/D-017）。

Workflow 保持确定性：审批对象的创建与 Task/Node 状态转换都发生在这里的 DB 副作用，
不在 Workflow 内直接写库。approve/reject/revoke 走 API ApprovalService + outbox →
Temporal Signal（见 app/api/routes/approvals.py + outbox_dispatch），不在此处。
"""

from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from app.activities.execution_seam import ExecutionUnit
from app.approval.schemas import ApprovalScope
from app.approval.service import ApprovalService
from app.domain.errors import IllegalTransitionError, StaleVersionError
from app.domain.repository import RunRepository, TaskRepository
from app.infra.deps import get_session_factory


@dataclass
class RequestApprovalInput:
    task_id: int
    user_id: int
    run_id: int
    spec_version: int
    plan_version: int
    unit: ExecutionUnit


@dataclass
class RequestApprovalResult:
    approval_id: int
    state: str


@activity.defn
async def request_approval(inp: RequestApprovalInput) -> RequestApprovalResult:
    session = get_session_factory()()
    try:
        run = RunRepository(session).get_owned(inp.user_id, inp.run_id)
        svc = ApprovalService(session)
        approval = svc.request_approval(
            user_id=inp.user_id,
            task_id=inp.task_id,
            spec_version=inp.spec_version,
            plan_version=inp.plan_version,
            node_id=inp.unit.node_id,
            node_type=inp.unit.node_type,
            action_type=inp.unit.approval_action_type or f"{inp.unit.node_type}_high_risk",
            target=inp.unit.approval_target,
            parameters=inp.unit.approval_parameters or {},
            scope=ApprovalScope.THIS_ACTION,
            status_payload={"run_id": run.id},
        )
        # Task RUNNING → WAITING_APPROVAL（状态机）
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        try:
            from app.domain.service import DomainService

            DomainService(TaskRepository(session)).transition_task(
                user_id=inp.user_id,
                task_id=inp.task_id,
                command="mark_waiting_approval",
                expected_version=task.version,
                actor_type="system",
                reason="high_risk_node_requires_approval",
            )
        except (IllegalTransitionError, StaleVersionError):
            pass  # 已在 WAITING_APPROVAL 视为幂等成功

        # Chat 时间线审批卡（D-039/D-042）：只追加 ref_type=approval 的指针消息，
        # 事实源始终是 Approval DB Object，不复制为第二条事实。
        from app.domain.repository import ChatMessageRepository

        ChatMessageRepository(session).create(
            user_id=inp.user_id,
            task_id=inp.task_id,
            role="system",
            content="该步骤需要审批后才能执行",
            ref_type="approval",
            ref_id=approval.id,
            meta={"action_type": approval.action_type, "state": approval.state},
        )
        session.commit()
        return RequestApprovalResult(approval_id=approval.id, state=approval.state)
    finally:
        session.close()


@dataclass
class BlockHighRiskNodeInput:
    task_id: int
    user_id: int
    run_id: int
    node_id: str | None


@activity.defn
async def block_high_risk_node(inp: BlockHighRiskNodeInput) -> None:
    """Reject/无实现时把高风险 Node 记录为 BLOCKED；绝不执行原高风险动作。

    D-017 / 三十五：用户 Reject 后 Workflow 不得偷偷执行原高风险 Node；走合法
    block 路径并记录事件，继续可继续的路径。M-08 计划执行暂不创建 NodeRun 行
    （M-09+ 创建），因此这里以 DomainEvent 记录 block 语义，不伪造 NodeRun。
    """
    session = get_session_factory()()
    try:
        task = TaskRepository(session).get_owned(inp.user_id, inp.task_id)
        from app.state.events import append_domain_event

        append_domain_event(
            session,
            user_id=inp.user_id,
            aggregate_type="task",
            aggregate_id=inp.task_id,
            event_type="node.blocked_high_risk",
            aggregate_version=task.version,
            payload={"node_id": inp.node_id, "reason": "approval_rejected_or_executor_unavailable"},
            actor_type="system",
        )
        session.commit()
    finally:
        session.close()
