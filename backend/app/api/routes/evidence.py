"""M-14 Evidence Query + secure content API（D-056/D-064）。

GET /tasks/{task_id}/evidence/{snapshot_id}              → EvidenceView（历史快照事实）
GET /tasks/{task_id}/evidence/{snapshot_id}/content      → 存储对象字节（owner 校验后，
                                                          从 ObjectStorage 读取，绝不 live fetch）
owner-safe：任务/证据越权统一 404。DTO 契约来自 app.evidence.contracts。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import require_user
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.evidence.contracts import EvidenceView
from app.evidence.service import EvidenceService
from app.infra.deps import get_db, storage
from app.infra.object_storage import ObjectStorage

router = APIRouter(prefix="/tasks/{task_id}/evidence", tags=["evidence"])


@router.get("/{snapshot_id}", response_model=EvidenceView)
def get_evidence(
    task_id: int,
    snapshot_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    object_storage: ObjectStorage = Depends(storage),
) -> EvidenceView:
    # owner-safe：任务越权 → 404（证据自身再按 user+task 校验）
    TaskRepository(db).get_owned(user.id, task_id)
    return EvidenceService(db, object_storage).get(
        user_id=user.id, task_id=task_id, snapshot_id=snapshot_id
    )


@router.get("/{snapshot_id}/content")
async def get_evidence_content(
    task_id: int,
    snapshot_id: int,
    download: bool = False,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    object_storage: ObjectStorage = Depends(storage),
) -> StreamingResponse:
    TaskRepository(db).get_owned(user.id, task_id)
    data, content_type = await EvidenceService(db, object_storage).content(
        user_id=user.id, task_id=task_id, snapshot_id=snapshot_id
    )
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="evidence-{snapshot_id}"'
    return StreamingResponse(iter([data]), media_type=content_type, headers=headers)
