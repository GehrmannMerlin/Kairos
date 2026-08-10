"""Task shell query API (M-05). Thin read-only owner-safe queries.

No task commands here — create/chat/spec/plan/run arrive in M-06+. Cross-user
access raises 404 (NOT_FOUND) through ``TaskRepository.get_owned`` so the
existence of another user's task is never revealed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.api.schemas import (
    AddSeedUrlCommand,
    ChatListResponse,
    ChatMessageDto,
    CreateMessageCommand,
    CreateMessageResponse,
    CreateTaskCommand,
    CreateTaskResponse,
    SpecDraftResponse,
    TaskShellDto,
    TaskShellListResponse,
    UpdateSpecDraftCommand,
)
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.models import ChatMessage, Task
from app.domain.repository import TaskRepository
from app.domain.task_draft import TaskDraftService
from app.infra.deps import get_db
from app.state.states import TaskState, allowed_task_actions

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_draft_service(db: DbSession = Depends(get_db)) -> TaskDraftService:
    return TaskDraftService(db)


def _chat_dto(message: ChatMessage) -> ChatMessageDto:
    return ChatMessageDto(
        id=message.id,
        role=message.role,
        content=message.content,
        ref_type=message.ref_type,
        ref_id=message.ref_id,
        meta=message.meta,
        created_at=message.created_at,
    )


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


# ---- M-06 Task Draft / Chat / Spec Draft commands ----


@router.post("", response_model=CreateTaskResponse, status_code=201)
def create_draft(
    cmd: CreateTaskCommand,
    user: User = Depends(require_user),
    service: TaskDraftService = Depends(get_task_draft_service),
) -> CreateTaskResponse:
    """+ 新任务 (empty) or Workbench direct input (Task Draft + first User message)."""
    if cmd.content:
        task, _ = service.create_draft_with_message(
            user_id=user.id,
            content=cmd.content,
            seed_urls=cmd.seed_urls or None,
            idempotency_key=cmd.idempotency_key,
        )
    else:
        task = service.create_empty_draft(user_id=user.id)
    return CreateTaskResponse(task_id=task.id)


@router.get("/{task_id}/chat", response_model=ChatListResponse)
def get_chat(
    task_id: int,
    user: User = Depends(require_user),
    service: TaskDraftService = Depends(get_task_draft_service),
) -> ChatListResponse:
    messages = service.list_messages(user_id=user.id, task_id=task_id)
    return ChatListResponse(messages=[_chat_dto(m) for m in messages])


@router.post("/{task_id}/messages", response_model=CreateMessageResponse, status_code=201)
def send_message(
    task_id: int,
    cmd: CreateMessageCommand,
    user: User = Depends(require_user),
    service: TaskDraftService = Depends(get_task_draft_service),
) -> CreateMessageResponse:
    message = service.append_user_message(
        user_id=user.id, task_id=task_id, content=cmd.content, idempotency_key=cmd.idempotency_key
    )
    return CreateMessageResponse(message=_chat_dto(message))


@router.get("/{task_id}/spec-draft", response_model=SpecDraftResponse)
def get_spec_draft(
    task_id: int,
    user: User = Depends(require_user),
    service: TaskDraftService = Depends(get_task_draft_service),
) -> SpecDraftResponse:
    payload = service.get_spec_draft(user_id=user.id, task_id=task_id)
    return SpecDraftResponse(task_id=task_id, payload=payload)


@router.put("/{task_id}/spec-draft", response_model=SpecDraftResponse)
def update_spec_draft(
    task_id: int,
    cmd: UpdateSpecDraftCommand,
    user: User = Depends(require_user),
    service: TaskDraftService = Depends(get_task_draft_service),
) -> SpecDraftResponse:
    payload = service.save_spec_draft(user_id=user.id, task_id=task_id, payload=cmd.payload)
    return SpecDraftResponse(task_id=task_id, payload=payload)


@router.post("/{task_id}/seed-urls", response_model=SpecDraftResponse)
def add_seed_url(
    task_id: int,
    cmd: AddSeedUrlCommand,
    user: User = Depends(require_user),
    service: TaskDraftService = Depends(get_task_draft_service),
) -> SpecDraftResponse:
    payload = service.add_seed_url(user_id=user.id, task_id=task_id, url=cmd.url)
    return SpecDraftResponse(task_id=task_id, payload=payload)
