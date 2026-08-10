"""Task shell query API (M-05). Thin read-only owner-safe queries.

No task commands here — create/chat/spec/plan/run arrive in M-06+. Cross-user
access raises 404 (NOT_FOUND) through ``TaskRepository.get_owned`` so the
existence of another user's task is never revealed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.agents.deps import get_goal_understanding_service
from app.agents.service import GoalUnderstandingService
from app.api.routes.templates import get_template_service
from app.api.schemas import (
    AddSeedUrlCommand,
    ChatListResponse,
    ChatMessageDto,
    ConfirmSpecCommand,
    ConfirmSpecResponse,
    CreateMessageCommand,
    CreateMessageResponse,
    CreateTaskCommand,
    CreateTaskResponse,
    SpecDraftResponse,
    TaskShellDto,
    TaskShellListResponse,
    TemplateDto,
    UnderstandResponse,
    UpdateSpecDraftCommand,
)
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.errors import SpecValidationError
from app.domain.models import ChatMessage, Task
from app.domain.repository import TaskRepository
from app.domain.service import DomainService
from app.domain.task_draft import TaskDraftService
from app.domain.template_service import TemplateService
from app.infra.deps import get_db
from app.state.states import TaskState, allowed_task_actions

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_draft_service(db: DbSession = Depends(get_db)) -> TaskDraftService:
    return TaskDraftService(db)


def get_domain_service(db: DbSession = Depends(get_db)) -> DomainService:
    return DomainService(TaskRepository(db))


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
        template_id=task.template_id,
        template_version=task.template_version,
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
    elif cmd.seed_urls:
        task = service.create_empty_draft(user_id=user.id)
        for url in cmd.seed_urls:
            service.add_seed_url(user_id=user.id, task_id=task.id, url=url)
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


@router.post("/{task_id}/template", response_model=TemplateDto)
def create_template_from_task(
    task_id: int,
    user: User = Depends(require_user),
    service: TemplateService = Depends(get_template_service),
) -> TemplateDto:
    """D-047: save a confirmed Spec as a template (owner-safe)."""
    row = service.create_from_task(user_id=user.id, task_id=task_id)
    return TemplateDto(
        template_id=row.template_id,
        version=row.version,
        name=row.name,
        task_type=row.task_type,
        goal_template=row.goal_template,
        variables=row.variables,
        field_schema=row.field_schema,
        completion_conditions=row.completion_conditions,
        advanced_settings=row.advanced_settings,
        field_expansion=row.field_expansion,
        is_favorite=row.is_favorite,
        created_at=row.created_at,
    )


@router.post("/{task_id}/understand", response_model=UnderstandResponse)
async def understand_task(
    task_id: int,
    user: User = Depends(require_user),
    service: GoalUnderstandingService = Depends(get_goal_understanding_service),
) -> UnderstandResponse:
    """Run Goal Understanding once. Errors keep the Draft + user message (D-066)."""
    outcome = await service.understand_for_task(user=user, task_id=task_id)
    return UnderstandResponse(
        task_id=task_id,
        message=_chat_dto(outcome.message),
        result=outcome.result.model_dump(mode="json"),
        spec_draft=outcome.spec_draft,
    )


@router.post("/{task_id}/spec-confirm", response_model=ConfirmSpecResponse)
def confirm_spec(
    task_id: int,
    cmd: ConfirmSpecCommand,
    user: User = Depends(require_user),
    domain: DomainService = Depends(get_domain_service),
    drafts: TaskDraftService = Depends(get_task_draft_service),
) -> ConfirmSpecResponse:
    """Freeze an immutable CollectionSpecVersion (D-004). Saving a draft != confirming."""
    payload = cmd.payload or drafts.get_spec_draft(user_id=user.id, task_id=task_id)
    if payload is None:
        raise SpecValidationError("请先保存采集方案")
    row = domain.confirm_spec(
        user_id=user.id,
        task_id=task_id,
        expected_version=cmd.expected_version,
        spec_payload=payload,
        actor_id=user.id,
    )
    task = drafts.get_task(user_id=user.id, task_id=task_id)
    return ConfirmSpecResponse(task_id=task_id, spec_version=row.version, state=task.state)
