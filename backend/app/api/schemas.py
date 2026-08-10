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
    task_type: str
    current_spec_version: int | None
    current_plan_version: int | None
    allowed_actions: list[str]
    created_at: datetime
    updated_at: datetime


class TaskShellListResponse(BaseModel):
    tasks: list[TaskShellDto]
