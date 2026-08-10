"""Task shell query API (M-05). Thin read-only owner-safe queries.

No task commands here — create/chat/spec/plan/run arrive in M-06+. Cross-user
access raises 404 (NOT_FOUND) through ``TaskRepository.get_owned`` so the
existence of another user's task is never revealed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.api.schemas import TaskShellDto, TaskShellListResponse
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.models import Task
from app.domain.repository import TaskRepository
from app.infra.deps import get_db
from app.state.states import TaskState, allowed_task_actions

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _shell_dto(task: Task) -> TaskShellDto:
    return TaskShellDto(
        task_id=task.id,
        title=task.title,
        state=task.state,
        version=task.version,
        task_type=task.task_type,
        current_spec_version=task.current_spec_version,
        current_plan_version=task.current_plan_version,
        allowed_actions=allowed_task_actions(TaskState(task.state)),
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@router.get("", response_model=TaskShellListResponse)
def list_tasks(
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> TaskShellListResponse:
    tasks = TaskRepository(db).list_by_user(user.id)
    return TaskShellListResponse(tasks=[_shell_dto(t) for t in tasks])


@router.get("/{task_id}", response_model=TaskShellDto)
def get_task(
    task_id: int,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> TaskShellDto:
    task = TaskRepository(db).get_owned(user.id, task_id)
    return _shell_dto(task)
