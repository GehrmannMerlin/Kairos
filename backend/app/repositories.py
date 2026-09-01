from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select

from app.db import session_scope
from app.domain import EventEnvelope, TaskRunStatus, TaskStatus, WorkspaceMetadata, WorkspacePermission
from app.models import AgentEvent, Task, TaskRun, Workspace


def workspace_metadata_from_model(row: Workspace) -> WorkspaceMetadata:
    return WorkspaceMetadata(
        workspace_id=row.workspace_id,
        owner_id=row.owner_id,
        display_name=row.display_name,
        root_path=row.root_path,
        permission_mode=WorkspacePermission(row.permission_mode),
        enabled=row.enabled,
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
