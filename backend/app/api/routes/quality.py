"""M-14 Quality Query API（D-062）。

GET /tasks/{task_id}/quality → QualityView（只读诊断，无任何数据修改能力）。
owner-safe：任务越权 → 404。DTO 契约来自 app.quality.contracts。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import require_user
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.infra.deps import get_db
from app.quality.contracts import QualityView
from app.quality.service import QualityService

router = APIRouter(prefix="/tasks/{task_id}/quality", tags=["quality"])


@router.get("", response_model=QualityView)
def get_quality(
    task_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> QualityView:
    # owner-safe：无权限/不存在 → 404
    TaskRepository(db).get_owned(user.id, task_id)
    return QualityService(db).assemble(user_id=user.id, task_id=task_id)
