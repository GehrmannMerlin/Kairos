"""Cross-module API response DTOs (M-05 task shell query)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TaskShellDto(BaseModel):
    """Owner-safe read-only snapshot of a Task for the frontend shell.

    state / allowed_actions come from the M-04 state machine; the frontend must
    not re-derive them locally.
    """

    task_id: int
    title: str
    state: str
    version: int
    task_type: str | None
    current_spec_version: int | None
    current_plan_version: int | None
    template_id: str | None
    template_version: int | None
    allowed_actions: list[str]
    created_at: datetime
    updated_at: datetime


class TaskShellListResponse(BaseModel):
    tasks: list[TaskShellDto]


class CreateTaskCommand(BaseModel):
    """Create a Task Draft; content present = create + first user message (D-045)."""

    content: str | None = None
    seed_urls: list[str] = []
    idempotency_key: str | None = None


class CreateTaskResponse(BaseModel):
    task_id: int


class ChatMessageDto(BaseModel):
    id: int
    role: str
    content: str
    ref_type: str | None
    ref_id: int | None
    meta: dict | None
    created_at: datetime


class ChatListResponse(BaseModel):
    messages: list[ChatMessageDto]


class CreateMessageCommand(BaseModel):
    content: str
    idempotency_key: str | None = None


class CreateMessageResponse(BaseModel):
    message: ChatMessageDto


class SpecDraftResponse(BaseModel):
    task_id: int
    payload: dict | None


class UpdateSpecDraftCommand(BaseModel):
    payload: dict


class AddSeedUrlCommand(BaseModel):
    url: str


class UnderstandResponse(BaseModel):
    task_id: int
    message: ChatMessageDto
    result: dict
    spec_draft: dict


class ConfirmSpecCommand(BaseModel):
    """expected_version is the Task.version the client last saw (optimistic lock)."""

    expected_version: int
    payload: dict | None = None


class ConfirmSpecResponse(BaseModel):
    task_id: int
    spec_version: int
    state: str


class TemplateDto(BaseModel):
    template_id: str
    version: int
    name: str
    task_type: str
    goal_template: str
    variables: list
    field_schema: list
    completion_conditions: list
    advanced_settings: dict
    field_expansion: dict
    is_favorite: bool
    created_at: datetime


class TemplateListResponse(BaseModel):
    templates: list[TemplateDto]


class TemplateFavoriteCommand(BaseModel):
    favorite: bool


class UseTemplateCommand(BaseModel):
    variables: dict[str, str] = {}


class UseTemplateResponse(BaseModel):
    task_id: int


class CreateTemplateFromTaskCommand(BaseModel):
    task_id: int
