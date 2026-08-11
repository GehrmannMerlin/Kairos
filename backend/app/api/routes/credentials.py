"""网站凭据 API（M-10 / D-059 / D-017 / D-057）。

Route 只做 auth/DTO 映射；存储语义在 WebsiteCredentialService（vault 加密），审批在
ApprovalService（credential_access，本任务范围 + fingerprint 绑定）。永不回读明文；
DTO 只含 credential_id/type/domain/scope/masked。
"""

from __future__ import annotations

from contextlib import suppress

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.api.schemas import (
    WebsiteCredentialCommand,
    WebsiteCredentialDto,
    WebsiteCredentialListResponse,
    WebsiteCredentialResponse,
)
from app.approval.schemas import ApprovalScope
from app.approval.service import ApprovalService
from app.auth.deps import require_user
from app.auth.models import User
from app.crawling.credentials import WebsiteCredentialService
from app.credentials.vault import CredentialVault
from app.domain.errors import IllegalTransitionError, StaleVersionError
from app.domain.repository import TaskRepository
from app.domain.service import DomainService
from app.infra.deps import get_db
from app.providers.deps import get_credential_vault

router = APIRouter(prefix="/tasks", tags=["credentials"])
saved_router = APIRouter(prefix="/credentials", tags=["credentials"])


def _service(db: DbSession, vault: CredentialVault) -> WebsiteCredentialService:
    return WebsiteCredentialService(db, vault)


def _dto(meta: dict) -> WebsiteCredentialDto:
    return WebsiteCredentialDto(
        credential_id=meta["credential_id"],
        type=meta["type"],
        domain=meta.get("domain"),
        scope=meta.get("scope"),
        task_id=meta.get("task_id"),
        masked=meta.get("masked", ""),
        created_at=meta.get("created_at"),
    )


@router.post("/{task_id}/credentials", response_model=WebsiteCredentialResponse)
def store_task_credential(
    task_id: int,
    cmd: WebsiteCredentialCommand,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    vault: CredentialVault = Depends(get_credential_vault),
) -> WebsiteCredentialResponse:
    TaskRepository(db).get_owned(user.id, task_id)
    service = _service(db, vault)
    if cmd.from_saved_credential_id is not None:
        meta = service.store_from_saved(
            user_id=user.id,
            task_id=task_id,
            saved_credential_id=cmd.from_saved_credential_id,
        )
    else:
        meta = service.store(
            user_id=user.id,
            task_id=task_id,
            ctype=cmd.type,
            payload=cmd.payload,
            scope=cmd.scope,
            domain=cmd.domain,
        )
    # 真正使用非公开凭据前必须 Approval（三十九）：credential_access，本任务范围 + fingerprint
    task = TaskRepository(db).get_owned(user.id, task_id)
    approval = ApprovalService(db).request_approval(
        user_id=user.id,
        task_id=task_id,
        spec_version=task.current_spec_version or 1,
        plan_version=task.current_plan_version,
        node_id=None,
        node_type="fetch",
        action_type="credential_access",
        target=f"{meta.get('domain') or ''}",
        parameters={"task_id": task_id, "domain": meta.get("domain"), "type": meta.get("type")},
        scope=ApprovalScope.THIS_ACTION,
        credential_ref=meta,
    )
    # 幂等：已在 WAITING_APPROVAL 视为成功
    with suppress(IllegalTransitionError, StaleVersionError):
        DomainService(TaskRepository(db)).transition_task(
            user_id=user.id,
            task_id=task_id,
            command="mark_waiting_approval",
            expected_version=task.version,
            actor_type="user",
            reason="credential_access",
        )
    return WebsiteCredentialResponse(credential=_dto(meta), approval_id=approval.id)


@router.get("/{task_id}/credentials", response_model=WebsiteCredentialListResponse)
def list_task_credentials(
    task_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    vault: CredentialVault = Depends(get_credential_vault),
) -> WebsiteCredentialListResponse:
    TaskRepository(db).get_owned(user.id, task_id)
    metas = _service(db, vault).list_for_task(user_id=user.id, task_id=task_id)
    return WebsiteCredentialListResponse(credentials=[_dto(m) for m in metas])


@router.delete("/{task_id}/credentials/{credential_id}")
def delete_task_credential(
    task_id: int,
    credential_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    vault: CredentialVault = Depends(get_credential_vault),
) -> dict:
    TaskRepository(db).get_owned(user.id, task_id)
    _service(db, vault).delete(user_id=user.id, credential_id=credential_id)
    return {"ok": True}


@saved_router.get("/saved", response_model=WebsiteCredentialListResponse)
def list_saved_credentials(
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    vault: CredentialVault = Depends(get_credential_vault),
) -> WebsiteCredentialListResponse:
    """设置 → 安全 → 已保存网站凭据（D-059）：只显示域名/类型/时间/删除入口。"""
    metas = _service(db, vault).list_saved_for_user(user_id=user.id)
    return WebsiteCredentialListResponse(credentials=[_dto(m) for m in metas])


@saved_router.delete("/{credential_id}")
def delete_saved_credential(
    credential_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    vault: CredentialVault = Depends(get_credential_vault),
) -> dict:
    _service(db, vault).delete(user_id=user.id, credential_id=credential_id)
    return {"ok": True}
