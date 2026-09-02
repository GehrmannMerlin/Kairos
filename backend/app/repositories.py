from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select

from app.db import session_scope
from app.domain import (
    CollectionError,
    CollectionExecutionContext,
    CollectionFieldSpec,
    CollectionMode,
    CollectionSourceStatus,
    CollectionSourceSummary,
    CollectionSpecConfirm,
    EventEnvelope,
    TaskRunStatus,
    TaskStatus,
    WorkspaceMetadata,
    WorkspacePermission,
)
from app.domain import (
    CollectionSpecVersion as CollectionSpecVersionData,
)
from app.models import (
    AgentEvent,
    CollectionSource,
    CollectionSpecVersion,
    Task,
    TaskRun,
    Workspace,
)
from app.url_policy import canonicalize_url, validate_public_http_url


def workspace_metadata_from_model(row: Workspace) -> WorkspaceMetadata:
    return WorkspaceMetadata(
        workspace_id=row.workspace_id,
        owner_id=row.owner_id,
        display_name=row.display_name,
        root_path=row.root_path,
        permission_mode=WorkspacePermission(row.permission_mode),
        enabled=row.enabled,
    )


def collection_spec_from_model(row: CollectionSpecVersion) -> CollectionSpecVersionData:
    return CollectionSpecVersionData(
        spec_version_id=row.spec_version_id,
        owner_id=row.owner_id,
        task_id=row.task_id,
        version=row.version,
        mode=CollectionMode(row.mode),
        goal=row.goal,
        fields=[CollectionFieldSpec.model_validate(value) for value in row.fields_json],
        seed_urls=list(row.seed_urls_json),
        target_count=row.target_count,
        confirmed_at=row.confirmed_at,
        created_at=row.created_at,
    )


def collection_source_summary_from_model(row: CollectionSource) -> CollectionSourceSummary:
    return CollectionSourceSummary(
        source_id=row.source_id,
        url=row.url,
        canonical_url=row.canonical_url,
        origin=row.origin,
        status=CollectionSourceStatus(row.status),
        snapshot_id=row.snapshot_id,
        failure_code=row.failure_code,
    )


async def confirm_collection_spec(
    task_id: str, owner_id: str, confirm: CollectionSpecConfirm
) -> CollectionSpecVersionData:
    async with session_scope() as session:
        async with session.begin():
            task = await session.scalar(
                select(Task).where(Task.task_id == task_id, Task.owner_id == owner_id).with_for_update()
            )
            if task is None:
                raise CollectionError("TASK_NOT_FOUND", "task not found")
            if task.status != TaskStatus.DRAFT.value:
                raise CollectionError("TASK_NOT_CONFIGURABLE", "task cannot change its collection spec")

            canonical_urls: list[str] = []
            seen: set[str] = set()
            for url in confirm.seed_urls:
                canonical = canonicalize_url(url)
                validate_public_http_url(canonical)
                if canonical not in seen:
                    seen.add(canonical)
                    canonical_urls.append(canonical)

            previous_version = await session.scalar(
                select(func.max(CollectionSpecVersion.version)).where(
                    CollectionSpecVersion.task_id == task_id,
                    CollectionSpecVersion.owner_id == owner_id,
                )
            )
            version = int(previous_version or 0) + 1
            now = datetime.now(UTC)
            spec_id = f"spec-{uuid4().hex}"
            spec_row = CollectionSpecVersion(
                spec_version_id=spec_id,
                owner_id=owner_id,
                task_id=task_id,
                version=version,
                mode=confirm.mode.value,
                goal=confirm.goal,
                fields_json=[field.model_dump(mode="json") for field in confirm.fields],
                seed_urls_json=canonical_urls,
                target_count=confirm.target_count,
                confirmed_at=now,
                created_at=now,
            )
            session.add(spec_row)
            await session.flush()
            for url in canonical_urls:
                session.add(
                    CollectionSource(
                        source_id=f"source-{uuid4().hex}",
                        owner_id=owner_id,
                        task_id=task_id,
                        spec_version_id=spec_id,
                        url=url,
                        canonical_url=url,
                        origin="SEED",
                        status=CollectionSourceStatus.PENDING.value,
                    )
                )
            task.spec_version_id = spec_id
        await session.refresh(spec_row)
        return collection_spec_from_model(spec_row)


async def get_collection_spec(task_id: str, owner_id: str) -> CollectionSpecVersionData | None:
    async with session_scope() as session:
        row = await session.scalar(
            select(CollectionSpecVersion)
            .join(Task, Task.spec_version_id == CollectionSpecVersion.spec_version_id)
            .where(
                Task.task_id == task_id,
                Task.owner_id == owner_id,
                CollectionSpecVersion.owner_id == owner_id,
            )
        )
        return collection_spec_from_model(row) if row else None


async def get_collection_context(
    task_id: str, task_run_id: str, owner_id: str, spec_version_id: str
) -> CollectionExecutionContext | None:
    async with session_scope() as session:
        run = await session.scalar(
            select(TaskRun).where(
                TaskRun.task_run_id == task_run_id,
                TaskRun.task_id == task_id,
                TaskRun.owner_id == owner_id,
            )
        )
        spec = await session.scalar(
            select(CollectionSpecVersion).where(
                CollectionSpecVersion.spec_version_id == spec_version_id,
                CollectionSpecVersion.task_id == task_id,
                CollectionSpecVersion.owner_id == owner_id,
            )
        )
        if run is None or spec is None:
            return None
        rows = await session.scalars(
            select(CollectionSource)
            .where(
                CollectionSource.task_id == task_id,
                CollectionSource.owner_id == owner_id,
                CollectionSource.spec_version_id == spec_version_id,
            )
            .order_by(CollectionSource.created_at, CollectionSource.source_id)
        )
        return CollectionExecutionContext(
            spec_version_id=spec.spec_version_id,
            mode=CollectionMode(spec.mode),
            goal=spec.goal,
            fields=[CollectionFieldSpec.model_validate(value) for value in spec.fields_json],
            sources=[collection_source_summary_from_model(row) for row in rows],
        )


async def get_workspace_metadata(workspace_id: str, owner_id: str) -> WorkspaceMetadata | None:
    async with session_scope() as session:
        row = await session.scalar(
            select(Workspace).where(Workspace.workspace_id == workspace_id, Workspace.owner_id == owner_id)
        )
        return workspace_metadata_from_model(row) if row else None


async def list_workspace_metadata(owner_id: str) -> list[WorkspaceMetadata]:
    async with session_scope() as session:
        rows = await session.scalars(
            select(Workspace).where(Workspace.owner_id == owner_id).order_by(Workspace.created_at)
        )
        return [workspace_metadata_from_model(row) for row in rows]


async def insert_workspace(metadata: WorkspaceMetadata) -> WorkspaceMetadata:
    async with session_scope() as session:
        session.add(
            Workspace(
                workspace_id=metadata.workspace_id,
                owner_id=metadata.owner_id,
                display_name=metadata.display_name,
                root_path=metadata.root_path,
                permission_mode=metadata.permission_mode.value,
                enabled=metadata.enabled,
            )
        )
        await session.commit()
    return metadata


async def insert_task(task_id: str, owner_id: str) -> Task:
    async with session_scope() as session:
        row = Task(task_id=task_id, owner_id=owner_id, status=TaskStatus.DRAFT.value)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def get_task(task_id: str, owner_id: str) -> Task | None:
    async with session_scope() as session:
        return await session.scalar(select(Task).where(Task.task_id == task_id, Task.owner_id == owner_id))


async def bind_task_workspace(task_id: str, owner_id: str, workspace_id: str | None) -> Task | None:
    async with session_scope() as session:
        row = await session.scalar(select(Task).where(Task.task_id == task_id, Task.owner_id == owner_id))
        if row is None:
            return None
        row.workspace_id = workspace_id
        await session.commit()
        await session.refresh(row)
        return row


async def create_task_run(
    task_id: str, owner_id: str, task_run_id: str, workflow_id: str, prompt: str
) -> TaskRun | None:
    async with session_scope() as session:
        task = await session.scalar(select(Task).where(Task.task_id == task_id, Task.owner_id == owner_id))
        if task is None:
            return None
        task.prompt = prompt
        task.status = TaskStatus.RUNNING.value
        row = TaskRun(
            task_run_id=task_run_id,
            task_id=task_id,
            owner_id=owner_id,
            workflow_id=workflow_id,
            status=TaskRunStatus.RUNNING.value,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def get_latest_task_run(task_id: str, owner_id: str) -> TaskRun | None:
    async with session_scope() as session:
        return await session.scalar(
            select(TaskRun)
            .where(TaskRun.task_id == task_id, TaskRun.owner_id == owner_id)
            .order_by(TaskRun.created_at.desc())
            .limit(1)
        )


async def update_task_run(
    task_run_id: str,
    status: TaskRunStatus,
    final_answer: str | None = None,
    error_message: str | None = None,
) -> None:
    async with session_scope() as session:
        run = await session.scalar(select(TaskRun).where(TaskRun.task_run_id == task_run_id))
        if run is None:
            return
        run.status = status.value
        run.final_answer = final_answer
        run.error_message = error_message
        task = await session.scalar(select(Task).where(Task.task_id == run.task_id))
        if task is not None:
            task.status = (
                TaskStatus.COMPLETED.value if status is TaskRunStatus.COMPLETED else TaskStatus.FAILED.value
            )
        await session.commit()


async def insert_agent_event(event: EventEnvelope) -> None:
    async with session_scope() as session:
        session.add(
            AgentEvent(
                task_id=event.task_id,
                task_run_id=event.task_run_id,
                owner_id=event.owner_id,
                event_type=event.event_type,
                agent_name=event.agent_name,
                tool_name=event.tool_name,
                summary=event.summary,
                payload=event.payload,
                occurred_at=event.occurred_at,
            )
        )
        await session.commit()


async def list_agent_events(
    task_id: str, owner_id: str, after_event_id: int = 0, limit: int = 100
) -> Sequence[AgentEvent]:
    async with session_scope() as session:
        rows = await session.scalars(
            select(AgentEvent)
            .where(
                AgentEvent.task_id == task_id,
                AgentEvent.owner_id == owner_id,
                AgentEvent.event_id > after_event_id,
            )
            .order_by(AgentEvent.event_id)
            .limit(limit)
        )
        return list(rows)
