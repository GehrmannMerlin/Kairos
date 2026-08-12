"""robots override 审批消费 Activity（M-09 / D-070 / D-017）。

Approved → ApprovalService.consume 复验 owner/spec/plan/fingerprint/expiry（D-017
失效规则）→ 通过则 URL → READY_FOR_FETCH，失败则 BLOCKED；Rejected → BLOCKED。
不创建新的 Approval 系统：完全复用 M-08 ApprovalService。
"""

from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from app.approval.service import ApprovalService
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.models import FrontierState
from app.infra.deps import get_session_factory


@dataclass
class ResolveRobotsOverrideInput:
    user_id: int
    task_id: int
    approval_id: int
    url_hash: str
    parameters: dict
    decision: str  # APPROVED | REJECTED


def _resolve_with_session(session, inp: ResolveRobotsOverrideInput) -> dict:
    """核心逻辑：consume 复验 + Frontier 状态迁移（可注入 session 供测试）。"""
    frontier = UrlFrontierRepository(session)
    if inp.decision == "APPROVED":
        try:
            ApprovalService(session).consume(
                user_id=inp.user_id, approval_id=inp.approval_id, parameters=inp.parameters
            )
            frontier.mark_state(
                user_id=inp.user_id,
                task_id=inp.task_id,
                url_hash=inp.url_hash,
                state=FrontierState.READY_FOR_FETCH,
            )
            return {"ok": True, "state": FrontierState.READY_FOR_FETCH.value}
        except Exception:
            session.rollback()
            frontier.mark_blocked(
                user_id=inp.user_id,
                task_id=inp.task_id,
                url_hash=inp.url_hash,
                reason="robots_override_revalidation_failed",
            )
            return {"ok": False, "state": FrontierState.BLOCKED.value}
    frontier.mark_blocked(
        user_id=inp.user_id,
        task_id=inp.task_id,
        url_hash=inp.url_hash,
        reason="robots_override_rejected",
    )
    return {"ok": False, "state": FrontierState.BLOCKED.value}


@activity.defn
async def resolve_robots_override(inp: ResolveRobotsOverrideInput) -> dict:
    session = get_session_factory()()
    try:
        return _resolve_with_session(session, inp)
    finally:
        session.close()
