"""M-14 Execution Query API（D-055/D-063）。

GET /tasks/{task_id}/execution                → ExecutionView（阶段摘要 + url/record 事实）
GET /tasks/{task_id}/execution/timeline       → TimelinePage（脱敏事件流，cursor 分页）
owner-safe：任务越权 → 404。DTO 契约来自 app.execution.contracts。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import require_user
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.execution.contracts import ExecutionView, TimelineCategory, TimelinePage
from app.execution.service import ExecutionService
from app.infra.deps import get_db

router = APIRouter(prefix="/tasks/{task_id}/execution", tags=["execution"])


@router.get("", response_model=ExecutionView)
def get_execution(
    task_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> ExecutionView:
    # owner-safe：无权限/不存在 → 404
    TaskRepository(db).get_owned(user.id, task_id)
    return ExecutionService(db).assemble_overview(user_id=user.id, task_id=task_id)


@router.get("/timeline", response_model=TimelinePage)
def get_timeline(
    task_id: int,
    category: TimelineCategory | None = Query(default=None),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> TimelinePage:
    TaskRepository(db).get_owned(user.id, task_id)
    service = ExecutionService(db)
    return service.timeline(
        user_id=user.id, task_id=task_id, category=category, after_id=after_id, limit=limit
    )
