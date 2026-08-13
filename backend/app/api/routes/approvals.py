"""Approval API routes: query / approve / reject / revoke（M-08 / D-017 / D-057）。

Route 只做 auth/DTO/response mapping；审批语义在 ApprovalService（owner + fingerprint
校验 + 状态转换 + DomainEvent/Outbox）。ApprovalResolution 信号由 Outbox dispatcher
在提交后分发到 M-07 TaskWorkflow，不在此处直接 Signal Temporal。
owner-safe：无权/不存在 → 404，不泄漏 Approval 存在性。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.api.schemas import (
    ApprovalDto,
    ApprovalListResponse,
    ApprovalResolutionCommand,
)
from app.approval.service import ApprovalService
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.models import Approval
from app.domain.repository import TaskRepository
from app.infra.deps import get_db
from app.infra.outbox_dispatch import OutboxTemporalDispatcher
from app.infra.temporal import get_temporal_client

router = APIRouter(prefix="/approvals", tags=["approvals"])
task_router = APIRouter(prefix="/tasks", tags=["approvals"])
logger = logging.getLogger(__name__)


def get_approval_service(db: DbSession = Depends(get_db)) -> ApprovalService:
    return ApprovalService(db)


def _dto(a: Approval) -> ApprovalDto:
    return ApprovalDto(
        approval_id=a.id,
        task_id=a.task_id,
        state=a.state,
        action_type=a.action_type,
        node_id=a.node_id,
        node_type=a.node_type,
        target=a.target,
        reason=a.reason,
        approved_scope=a.approved_scope,
        credential_ref=a.credential_ref,
        status_payload=a.status_payload,
        expires_at=a.expires_at,
        created_at=a.created_at,
    )


@router.get("/{approval_id}", response_model=ApprovalDto)
def get_approval(
    approval_id: int,
    user: User = Depends(require_user),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalDto:
    approval = service.get_owned(user.id, approval_id)
    return _dto(approval)


@router.post("/{approval_id}/approve", response_model=ApprovalDto)
async def approve(
    approval_id: int,
    cmd: ApprovalResolutionCommand,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalDto:
    approval = service.get_owned(user.id, approval_id)
    TaskRepository(db).get_owned(user.id, approval.task_id)
    resolved = service.approve(user_id=user.id, approval_id=approval_id, actor_id=user.id)
    await _dispatch(db, user.id, approval.task_id)
    return _dto(resolved)


@router.post("/{approval_id}/reject", response_model=ApprovalDto)
async def reject(
    approval_id: int,
    cmd: ApprovalResolutionCommand,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalDto:
    approval = service.get_owned(user.id, approval_id)
    TaskRepository(db).get_owned(user.id, approval.task_id)
    resolved = service.reject(user_id=user.id, approval_id=approval_id, actor_id=user.id)
    await _dispatch(db, user.id, approval.task_id)
    return _dto(resolved)


@router.post("/{approval_id}/revoke", response_model=ApprovalDto)
async def revoke(
    approval_id: int,
    cmd: ApprovalResolutionCommand,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalDto:
    approval = service.get_owned(user.id, approval_id)
    TaskRepository(db).get_owned(user.id, approval.task_id)
    resolved = service.revoke(user_id=user.id, approval_id=approval_id, actor_id=user.id)
    await _dispatch(db, user.id, approval.task_id)
    return _dto(resolved)


@task_router.get("/{task_id}/approvals", response_model=ApprovalListResponse)
def list_task_approvals(
    task_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalListResponse:
    TaskRepository(db).get_owned(user.id, task_id)
    approvals = service.list_for_task(user.id, task_id)
    return ApprovalListResponse(task_id=task_id, approvals=[_dto(a) for a in approvals])


@task_router.get("/{task_id}/approvals/pending", response_model=ApprovalListResponse)
def list_pending_task_approvals(
    task_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalListResponse:
    TaskRepository(db).get_owned(user.id, task_id)
    approvals = service.list_pending_for_task(user.id, task_id)
    return ApprovalListResponse(task_id=task_id, approvals=[_dto(a) for a in approvals])


async def _dispatch(db: DbSession, user_id: int, task_id: int) -> None:
    """把本 task 的 approval.* outbox 分发为 Temporal Signal；失败不阻塞响应。"""
    try:
        client = await get_temporal_client()
        await OutboxTemporalDispatcher(client).dispatch_pending_for(
            db, user_id=user_id, task_id=task_id
        )
    except Exception:
        logger.warning(
            "Temporal signal dispatch failed for approval on task %s; outbox retained",
            task_id,
            exc_info=True,
        )
