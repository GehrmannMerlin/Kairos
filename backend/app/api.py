from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any
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
from app.domain import (
    CollectionError,
    CollectionProgress,
    CollectionSourceSummary,
    CollectionSpecConfirm,
    CollectionSpecVersion,
    RecordStatus,
    SearchRoundSummary,
    TaskRunStatus,
    TaskStatus,
    WorkspaceMetadata,
    WorkspacePermission,
)
from app.repositories import (
    _search_round_summary_from_model,
    bind_task_workspace,
    confirm_collection_spec,
    create_task_run,
    get_collection_progress,
    get_collection_record,
    get_collection_spec,
    get_latest_task_run,
    get_snapshot_metadata,
    get_task,
    get_workspace_metadata,
    insert_task,
    insert_workspace,
    list_agent_events,
    list_collection_records,
    list_collection_sources,
    list_record_evidence,
    list_search_rounds,
    list_workspace_metadata,
    update_task_run,
)
from app.search import SearchProviderError, resolve_search_provider
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
    spec_version_id: str | None
    status: TaskStatus


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=20_000)
    model_config_id: str | None = None
    search_provider_config_id: str | None = None


class RunResponse(BaseModel):
    task_run_id: str
    workflow_id: str
    status: TaskRunStatus


class TaskDetailResponse(TaskResponse):
    latest_run: RunResponse | None = None
    final_answer: str | None = None
    error_message: str | None = None


class CollectionSpecResponse(CollectionSpecVersion):
    sources: list[CollectionSourceSummary]


class RecordResponse(BaseModel):
    record_id: str
    snapshot_id: str
    ordinal: int
    fields: dict[str, Any]
    status: RecordStatus
    validation_issues: list[str]
    canonical_record_id: str | None = None


class EvidenceResponse(BaseModel):
    evidence_id: str
    field_name: str
    value: Any = None
    source_url: str
    quote: str
    verified: bool
    confidence: float | None


class SearchProviderAvailabilityResponse(BaseModel):
    provider: str
    display_name: str
    configured: bool


class SnapshotMetadataResponse(BaseModel):
    snapshot_id: str
    url: str
    canonical_url: str
    status_code: int
    content_type: str
    title: str | None
    content_hash: str
    bytes_read: int
    text_chars: int
    text_preview: str
    captured_at: datetime


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
        spec_version_id=task.spec_version_id,  # type: ignore[attr-defined]
        status=TaskStatus(task.status),  # type: ignore[attr-defined]
    )


def _collection_http_error(exc: CollectionError) -> HTTPException:
    if exc.code.endswith("NOT_FOUND") or exc.code in {"SOURCE_OUT_OF_SCOPE", "SNAPSHOT_NOT_FOUND"}:
        code = status.HTTP_404_NOT_FOUND
    elif exc.code in {"ALREADY_COMMITTED_DIFFERENT_PAYLOAD", "TASK_NOT_CONFIGURABLE"}:
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail={"code": exc.code, "message": exc.message})


async def _collection_spec_response(
    spec: CollectionSpecVersion,
) -> CollectionSpecResponse:
    rows = await list_collection_sources(spec.task_id, spec.owner_id, spec.spec_version_id)
    return CollectionSpecResponse(
        **spec.model_dump(),
        sources=[
            CollectionSourceSummary(
                source_id=row.source_id,
                url=row.url,
                canonical_url=row.canonical_url,
                origin=row.origin,
                status=row.status,
                snapshot_id=row.snapshot_id,
                failure_code=row.failure_code,
                title=row.search_title,
                snippet=(row.search_snippet or "")[:500],
            )
            for row in rows
        ],
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


@app.post("/api/tasks/{task_id}/collection/spec", response_model=CollectionSpecResponse)
async def confirm_collection_spec_api(
    task_id: str,
    body: CollectionSpecConfirm,
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> CollectionSpecResponse:
    owner_id = _owner_id(x_kairos_user_id)
    if body.mode.value in {"EXPLORATORY", "HYBRID"}:
        try:
            resolve_search_provider()
        except SearchProviderError as exc:
            raise _collection_http_error(CollectionError(exc.code, exc.message)) from exc
    try:
        spec = await confirm_collection_spec(task_id, owner_id, body)
    except CollectionError as exc:
        raise _collection_http_error(exc) from exc
    return await _collection_spec_response(spec)


@app.get("/api/tasks/{task_id}/collection/spec", response_model=CollectionSpecResponse)
async def get_collection_spec_api(
    task_id: str,
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> CollectionSpecResponse:
    owner_id = _owner_id(x_kairos_user_id)
    spec = await get_collection_spec(task_id, owner_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="collection spec not found")
    return await _collection_spec_response(spec)


@app.get("/api/providers/search/availability", response_model=SearchProviderAvailabilityResponse)
async def search_provider_availability_api(
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> SearchProviderAvailabilityResponse:
    _owner_id(x_kairos_user_id)
    settings = get_settings()
    try:
        provider = resolve_search_provider()
    except SearchProviderError:
        return SearchProviderAvailabilityResponse(
            provider=settings.search_provider_type,
            display_name=settings.search_provider_type.title(),
            configured=False,
        )
    return SearchProviderAvailabilityResponse(
        provider=getattr(provider, "provider_name", settings.search_provider_type),
        display_name=getattr(provider, "provider_name", settings.search_provider_type).title(),
        configured=True,
    )


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
    if task.spec_version_id is not None:
        spec = await get_collection_spec(task_id, owner_id)
        if spec is not None and spec.mode.value in {"EXPLORATORY", "HYBRID"}:
            try:
                resolve_search_provider(body.search_provider_config_id)
            except SearchProviderError as exc:
                raise _collection_http_error(CollectionError(exc.code, exc.message)) from exc
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
        spec_version_id=task.spec_version_id,
        workspace_id=workspace_id,
        workspace_permission=workspace_permission,
        model_config_id=model_config_id,
        search_provider_config_id=body.search_provider_config_id,
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


@app.get("/api/tasks/{task_id}/collection/progress", response_model=CollectionProgress)
async def collection_progress_api(
    task_id: str,
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> CollectionProgress:
    owner_id = _owner_id(x_kairos_user_id)
    task = await get_task(task_id, owner_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.spec_version_id is None:
        raise HTTPException(status_code=404, detail="collection spec not found")
    run = await get_latest_task_run(task_id, owner_id)
    if run is None:
        raise HTTPException(status_code=404, detail="collection run not found")
    try:
        return await get_collection_progress(task_id, run.task_run_id, owner_id, task.spec_version_id)
    except CollectionError as exc:
        raise _collection_http_error(exc) from exc


@app.get("/api/tasks/{task_id}/collection/search-rounds")
async def collection_search_rounds_api(
    task_id: str,
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> list[SearchRoundSummary]:
    owner_id = _owner_id(x_kairos_user_id)
    task = await get_task(task_id, owner_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.spec_version_id is None:
        return []
    run = await get_latest_task_run(task_id, owner_id)
    if run is None:
        return []
    rows = await list_search_rounds(task_id, run.task_run_id, owner_id, task.spec_version_id)
    return [_search_round_summary_from_model(row) for row in rows]


@app.get("/api/tasks/{task_id}/records", response_model=list[RecordResponse])
async def collection_records_api(
    task_id: str,
    include_duplicates: bool = Query(default=False),
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> list[RecordResponse]:
    owner_id = _owner_id(x_kairos_user_id)
    task = await get_task(task_id, owner_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.spec_version_id is None:
        return []
    run = await get_latest_task_run(task_id, owner_id)
    if run is None:
        return []
    rows = await list_collection_records(
        task_id,
        run.task_run_id,
        owner_id,
        task.spec_version_id,
        include_duplicates=include_duplicates,
    )
    return [
        RecordResponse(
            record_id=row.record_id,
            snapshot_id=row.snapshot_id,
            ordinal=row.ordinal,
            fields=row.data_json,
            status=RecordStatus(row.status),
            validation_issues=row.validation_issues,
            canonical_record_id=row.canonical_record_id,
        )
        for row in rows
    ]


@app.get("/api/tasks/{task_id}/records/{record_id}/evidence", response_model=list[EvidenceResponse])
async def record_evidence_api(
    task_id: str,
    record_id: str,
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> list[EvidenceResponse]:
    owner_id = _owner_id(x_kairos_user_id)
    task = await get_task(task_id, owner_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    record = await get_collection_record(record_id, task_id, owner_id)
    if record is None:
        raise HTTPException(status_code=404, detail="record not found")
    evidence = await list_record_evidence(record_id, task_id, owner_id)
    return [
        EvidenceResponse(
            evidence_id=row.evidence_id,
            field_name=row.field_name,
            value=record.data_json.get(row.field_name),
            source_url=row.source_url,
            quote=row.quote,
            verified=row.verified,
            confidence=row.confidence,
        )
        for row in evidence
    ]


@app.get("/api/tasks/{task_id}/snapshots/{snapshot_id}", response_model=SnapshotMetadataResponse)
async def snapshot_metadata_api(
    task_id: str,
    snapshot_id: str,
    x_kairos_user_id: Annotated[str | None, Header()] = None,
) -> SnapshotMetadataResponse:
    owner_id = _owner_id(x_kairos_user_id)
    task = await get_task(task_id, owner_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    snapshot = await get_snapshot_metadata(snapshot_id, task_id, owner_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return SnapshotMetadataResponse(
        snapshot_id=snapshot.snapshot_id,
        url=snapshot.url,
        canonical_url=snapshot.canonical_url,
        status_code=snapshot.status_code,
        content_type=snapshot.content_type,
        title=snapshot.title,
        content_hash=snapshot.content_hash,
        bytes_read=snapshot.bytes_read,
        text_chars=snapshot.text_chars,
        text_preview=snapshot.text_preview[:2000],
        captured_at=snapshot.captured_at,
    )


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
