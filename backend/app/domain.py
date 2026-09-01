from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkspacePermission(StrEnum):
    NONE = "NONE"
    READ_ONLY = "READ_ONLY"
    READ_WRITE = "READ_WRITE"
    LOCAL_FULL_ACCESS = "LOCAL_FULL_ACCESS"


class TaskStatus(StrEnum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class WorkspaceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    owner_id: str
    display_name: str
    root_path: str
    permission_mode: WorkspacePermission
    enabled: bool = True


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    task_id: str
    task_run_id: str
    owner_id: str
    agent_name: str = "kairos-agent-v1"
    tool_name: str | None = None
    summary: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def bounded_payload(payload: dict[str, Any], *, max_chars: int = 4000) -> dict[str, Any]:
    """Keep event metadata bounded and exclude obvious secret-shaped fields."""
    blocked = {"api_key", "credential", "credential_plaintext", "password", "secret", "token"}
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in blocked or any(
            marker in key.lower() for marker in ("api_key", "password", "secret_key")
        ):
            continue
        if isinstance(value, str):
            clean[key] = value[:max_chars]
        elif isinstance(value, (int, float, bool)) or value is None:
            clean[key] = value
        else:
            text = str(value)
            clean[key] = text[:max_chars]
    return clean
