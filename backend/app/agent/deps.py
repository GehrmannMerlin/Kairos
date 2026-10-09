from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.domain import WorkspacePermission


class KairosAgentDeps(BaseModel):
    """Only durable identifiers and small values cross the Workflow boundary."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    task_id: str
    task_run_id: str
    spec_version_id: str | None = None
    workspace_id: str | None = None
    workspace_permission: WorkspacePermission = WorkspacePermission.NONE
    model_config_id: str
    search_provider_config_id: str | None = None
