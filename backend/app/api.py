from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from sqlalchemy import text
from sse_starlette.sse import EventSourceResponse
from temporalio.client import Client

from app.agent.deps import KairosAgentDeps
from app.config import get_settings
from app.db import session_scope
from app.domain import TaskRunStatus, TaskStatus, WorkspaceMetadata, WorkspacePermission
from app.repositories import (
    bind_task_workspace,
    create_task_run,
    get_latest_task_run,
    get_task,
    get_workspace_metadata,
    insert_task,
    insert_workspace,
    list_agent_events,
    list_workspace_metadata,
    update_task_run,
)
from app.workflows import KairosAgentWorkflow, KairosAgentWorkflowInput
from app.workspace import LocalFolderPicker, WorkspacePathResolver


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    root_path: str = Field(min_length=1, max_length=2000)
    permission_mode: WorkspacePermission


class WorkspaceResponse(WorkspaceMetadata):
    pass


class TaskResponse(BaseModel):
    task_id: str
    owner_id: str
    workspace_id: str | None
    status: TaskStatus


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=20_000)
    model_config_id: str | None = None


class RunResponse(BaseModel):
    task_run_id: str
    workflow_id: str
    status: TaskRunStatus


class TaskDetailResponse(TaskResponse):
    latest_run: RunResponse | None = None
    final_answer: str | None = None
    error_message: str | None = None


class LocalFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manual_path: str | None = Field(default=None, max_length=2000)


def _owner_id(header_value: str | None) -> str:
    # This explicit local owner header keeps the slice owner-scoped without inventing a second
    # production auth system. A future Auth dependency replaces this function, not the domain queries.
    settings = get_settings()
    if header_value:
        return header_value
    if settings.app_env == "local":
        return "local-user"
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")


def _task_response(row: object) -> TaskResponse:
    task = row
    return TaskResponse(
        task_id=task.task_id,  # type: ignore[attr-defined]
        owner_id=task.owner_id,  # type: ignore[attr-defined]
        workspace_id=task.workspace_id,  # type: ignore[attr-defined]
        status=TaskStatus(task.status),  # type: ignore[attr-defined]
    )


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    try:
        application.state.temporal_client = await Client.connect(
            settings.temporal_target,
            namespace=settings.temporal_namespace,
            plugins=[PydanticAIPlugin()],
        )
        application.state.temporal_connect_error = None
    except Exception as exc:
        application.state.temporal_client = None
        application.state.temporal_connect_error = type(exc).__name__
    yield


app = FastAPI(title="Kairos Agent Foundation", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    database_status = "ok"
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        database_status = "unavailable"
    temporal_status = "ok" if request.app.state.temporal_client is not None else "unavailable"
    return {
        "status": "ok" if database_status == temporal_status == "ok" else "degraded",
        "database": database_status,
        "temporal": temporal_status,
    }


@app.get("/api/workspaces", response_model=list[WorkspaceResponse])
async def list_workspaces(
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> list[WorkspaceResponse]:
    owner_id = _owner_id(x_kairos_user_id)
    return [
        WorkspaceResponse.model_validate(item.model_dump())
        for item in await list_workspace_metadata(owner_id)
    ]


@app.post("/api/workspaces", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreateRequest,
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> WorkspaceResponse:
    owner_id = _owner_id(x_kairos_user_id)
    if body.permission_mode is WorkspacePermission.LOCAL_FULL_ACCESS and not get_settings().local_mode:
        raise HTTPException(status_code=400, detail="LOCAL_FULL_ACCESS requires KAIROS_LOCAL_MODE=true")
    try:
        canonical_root = WorkspacePathResolver(body.root_path).root
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    metadata = WorkspaceMetadata(
        workspace_id=f"ws-{uuid4().hex}",
        owner_id=owner_id,
        display_name=body.display_name,
        root_path=str(canonical_root),
        permission_mode=body.permission_mode,
    )
    await insert_workspace(metadata)
    return WorkspaceResponse.model_validate(metadata.model_dump())


@app.post("/api/local/pick-folder")
async def pick_local_folder(
    body: LocalFolderRequest,
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    _owner_id(x_kairos_user_id)
    try:
        root = await asyncio.to_thread(LocalFolderPicker().pick, body.manual_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"root_path": str(root)}


@app.post("/api/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(x_kairos_user_id: Annotated[str | None, Header()] = None) -> TaskResponse:
    owner_id = _owner_id(x_kairos_user_id)
    row = await insert_task(f"task-{uuid4().hex}", owner_id)
    return _task_response(row)


@app.post("/api/tasks/{task_id}/workspace", response_model=TaskResponse)
async def bind_workspace(
    task_id: str,
    workspace_id: str,
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> TaskResponse:
    owner_id = _owner_id(x_kairos_user_id)
    if workspace_id:
        workspace = await get_workspace_metadata(workspace_id, owner_id)
        if workspace is None or not workspace.enabled:
            raise HTTPException(status_code=404, detail="workspace not found")
    row = await bind_task_workspace(task_id, owner_id, workspace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_response(row)


@app.get("/api/tasks/{task_id}", response_model=TaskDetailResponse)
async def task_detail(
    task_id: str,
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> TaskDetailResponse:
    owner_id = _owner_id(x_kairos_user_id)
    row = await get_task(task_id, owner_id)
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    run = await get_latest_task_run(task_id, owner_id)
    return TaskDetailResponse(
        **_task_response(row).model_dump(),
        latest_run=(
            RunResponse(
                task_run_id=run.task_run_id, workflow_id=run.workflow_id, status=TaskRunStatus(run.status)
            )
            if run
            else None
        ),
        final_answer=run.final_answer if run else None,
        error_message=run.error_message if run else None,
    )


@app.post("/api/tasks/{task_id}/runs", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    task_id: str,
    body: RunCreateRequest,
    request: Request,
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> RunResponse:
    owner_id = _owner_id(x_kairos_user_id)
    task = await get_task(task_id, owner_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    client: Client | None = request.app.state.temporal_client
    if client is None:
        raise HTTPException(status_code=503, detail="Temporal client is unavailable")
    settings = get_settings()
    workspace_id = task.workspace_id
    workspace_permission = WorkspacePermission.NONE
    if workspace_id is not None:
        workspace = await get_workspace_metadata(workspace_id, owner_id)
        if workspace is None or not workspace.enabled:
            raise HTTPException(status_code=409, detail="bound workspace is unavailable")
        workspace_permission = workspace.permission_mode
    task_run_id = f"run-{uuid4().hex}"
    workflow_id = f"kairos-task-{task_id}-{task_run_id}"
    model_config_id = body.model_config_id or settings.model_config_id
    deps = KairosAgentDeps(
        user_id=owner_id,
        task_id=task_id,
        task_run_id=task_run_id,
        workspace_id=workspace_id,
        workspace_permission=workspace_permission,
        model_config_id=model_config_id,
        search_provider_config_id=None,
    )
    created = await create_task_run(task_id, owner_id, task_run_id, workflow_id, body.prompt)
    if created is None:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        await client.start_workflow(
            KairosAgentWorkflow.run,
            KairosAgentWorkflowInput(deps=deps, prompt=body.prompt),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
    except Exception as exc:
        await update_task_run(task_run_id, TaskRunStatus.FAILED, error_message=type(exc).__name__)
        raise HTTPException(status_code=503, detail="Temporal workflow could not be started") from exc
    return RunResponse(task_run_id=task_run_id, workflow_id=workflow_id, status=TaskRunStatus.RUNNING)


@app.get("/api/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    x_kairos_user_id: Annotated[str | None, Header()] = None,
    after_id: int = Query(default=0, ge=0),
    run_id: str | None = Query(default=None),
) -> EventSourceResponse:
    owner_id = _owner_id(x_kairos_user_id)
    task = await get_task(task_id, owner_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        cursor = after_id
        idle_rounds = 0
        while idle_rounds < 120:
            rows = await list_agent_events(task_id, owner_id, cursor)
            if run_id is not None:
                rows = [row for row in rows if row.task_run_id == run_id]
            if rows:
                idle_rounds = 0
                for row in rows:
                    cursor = row.event_id
                    payload = {
                        "event_id": row.event_id,
                        "event_type": row.event_type,
                        "task_id": row.task_id,
                        "task_run_id": row.task_run_id,
                        "tool_name": row.tool_name,
                        "summary": row.summary,
                        "payload": row.payload,
                        "occurred_at": row.occurred_at.isoformat()
                        if isinstance(row.occurred_at, datetime)
                        else None,
                    }
                    yield {"id": str(row.event_id), "event": row.event_type, "data": json.dumps(payload)}
                    if row.event_type in {"run.completed", "run.failed"}:
                        return
            else:
                idle_rounds += 1
                await asyncio.sleep(get_settings().event_poll_seconds)

    return EventSourceResponse(event_stream(), ping=15)
