"""credential_access 审批消费 Activity（M-10 / D-017 / 三十九）。

Approved → ApprovalService.consume 复验 owner/spec/plan/fingerprint/expiry → 通过则把该
task 下 WAITING_CREDENTIAL 且 domain 匹配的 URL 迁移为 READY_FOR_FETCH（凭据批准后可再抓）；
Rejected/复验失败 → BLOCKED。完全复用 M-08 ApprovalService，不建第二套审批系统。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from temporalio import activity

from app.approval.service import ApprovalService
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.models import FrontierState
from app.infra.deps import get_session_factory


@dataclass
class ResolveCredentialAccessInput:
    user_id: int
    task_id: int
    approval_id: int
    url_hash: str
    parameters: dict
    decision: str  # APPROVED | REJECTED


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _resolve_with_session(session, inp: ResolveCredentialAccessInput) -> dict:
    frontier = UrlFrontierRepository(session)
    domain = str((inp.parameters or {}).get("domain") or "")
    task_id = inp.task_id
    if inp.decision == "APPROVED":
        try:
            ApprovalService(session).consume(
                user_id=inp.user_id, approval_id=inp.approval_id, parameters=inp.parameters
            )
        except Exception:
            session.rollback()
            frontier.mark_blocked(
                user_id=inp.user_id,
                task_id=inp.task_id,
                url_hash=inp.url_hash,
                reason="credential_approval_revalidation_failed",
            )
            return {"ok": False, "state": FrontierState.BLOCKED.value}
        # 该 task 下 WAITING_CREDENTIAL 且 host 匹配的 URL → READY_FOR_FETCH（凭据批准后可再抓）
        moved = 0
        if task_id:
            waiting = frontier.list_by_state(
                user_id=inp.user_id, task_id=task_id, state=FrontierState.WAITING_CREDENTIAL
            )
            for row in waiting:
                if not domain or _host_of(row.url) == domain:
                    frontier.mark_state(
                        user_id=inp.user_id,
                        task_id=row.task_id,
                        url_hash=row.url_hash,
                        state=FrontierState.READY_FOR_FETCH,
                    )
                    moved += 1
        return {"ok": True, "state": FrontierState.READY_FOR_FETCH.value, "moved": moved}
    frontier.mark_blocked(
        user_id=inp.user_id,
        task_id=inp.task_id,
        url_hash=inp.url_hash,
        reason="credential_approval_rejected",
    )
    return {"ok": False, "state": FrontierState.BLOCKED.value}


@activity.defn
async def resolve_credential_access(inp: ResolveCredentialAccessInput) -> dict:
    session = get_session_factory()()
    try:
        return _resolve_with_session(session, inp)
    finally:
        session.close()
