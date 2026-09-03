"""Owner-scoped BrowserTask persistence: claim / complete / fail / blocked / list.

One BrowserTask per (task_run_id, source_id). Only durable metadata is stored:
status, attempt count, snapshot reference, failure code/message, timestamps.
No live browser handle, CDP pointer, tab object, locator, or pid ever crosses
this boundary — the Chromium session lives only inside the Browser Activity.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.db import session_scope
from app.domain import BrowserTask as BrowserTaskData
from app.domain import (
    BrowserTaskStatus,
    BrowserTaskSummary,
    CollectionError,
    CollectionSourceStatus,
)
from app.models import BrowserTask, CollectionSource, TaskRun


def _browser_task_from_model(row: BrowserTask) -> BrowserTaskData:
    return BrowserTaskData(
        browser_task_id=row.browser_task_id,
        owner_id=row.owner_id,
        task_id=row.task_id,
        task_run_id=row.task_run_id,
        spec_version_id=row.spec_version_id,
        source_id=row.source_id,
        status=BrowserTaskStatus(row.status),
        attempt_count=int(row.attempt_count or 0),
        snapshot_id=row.snapshot_id,
        policy_version=row.policy_version,
        failure_code=row.failure_code,
        failure_message=row.failure_message,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def get_browser_task_for_source(
    task_id: str,
    task_run_id: str,
    owner_id: str,
    source_id: str,
) -> BrowserTaskData | None:
    async with session_scope() as session:
        row = await session.scalar(
            select(BrowserTask).where(
                BrowserTask.task_id == task_id,
                BrowserTask.task_run_id == task_run_id,
                BrowserTask.owner_id == owner_id,
                BrowserTask.source_id == source_id,
            )
        )
        return _browser_task_from_model(row) if row else None


async def claim_browser_task(
    *,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
    source_id: str,
    policy_version: str,
) -> BrowserTaskData | None:
    """Claim (or reuse) the single BrowserTask for a source.

    Returns an existing COMPLETED task when a snapshot already exists (repeat
    requests never re-open Chromium). Returns None when a RUNNING task exists
    (concurrent duplicate -> caller returns BROWSER_TASK_ALREADY_RUNNING).
    Otherwise creates a PENDING row and CAS-es it to RUNNING with attempt 1.

    Caller must have already resolved the source and validated budget/scope.
    """
    async with session_scope() as session:
        async with session.begin():
            source = await session.scalar(  # type: ignore[attr-defined]
                select(CollectionSource)
                .join(TaskRun, TaskRun.task_id == CollectionSource.task_id)
                .where(
                    CollectionSource.source_id == source_id,
                    CollectionSource.owner_id == owner_id,
                    CollectionSource.task_id == task_id,
                    TaskRun.task_run_id == task_run_id,
                )
                .with_for_update()
            )
            if source is None:
                raise CollectionError("SOURCE_NOT_FOUND", "collection source not found")
            existing = await session.scalar(  # type: ignore[attr-defined]
                select(BrowserTask).where(
                    BrowserTask.task_id == task_id,
                    BrowserTask.task_run_id == task_run_id,
                    BrowserTask.owner_id == owner_id,
                    BrowserTask.source_id == source_id,
                )
            )
            if existing is not None:
                if existing.status == BrowserTaskStatus.COMPLETED.value and existing.snapshot_id:
                    return _browser_task_from_model(existing)
                if existing.status == BrowserTaskStatus.RUNNING.value:
                    return None
                # PENDING / FAILED / BLOCKED with no completed snapshot: do not
                # auto-retry a BLOCKED task; a FAILED task may be re-claimed by a
                # fresh Temporal attempt (new Chromium session).
                if existing.status == BrowserTaskStatus.BLOCKED.value:
                    return _browser_task_from_model(existing)
                existing.status = BrowserTaskStatus.RUNNING.value
                existing.attempt_count = int(existing.attempt_count or 0) + 1
                existing.started_at = datetime.now(UTC)
                existing.failure_code = None
                existing.failure_message = None
                await session.flush()  # type: ignore[attr-defined]
                await session.refresh(existing)  # type: ignore[attr-defined]
                return _browser_task_from_model(existing)

            task = BrowserTask(
                browser_task_id=f"browser-task-{uuid4().hex}",
                owner_id=owner_id,
                task_id=task_id,
                task_run_id=task_run_id,
                spec_version_id=spec_version_id,
                source_id=source_id,
                status=BrowserTaskStatus.RUNNING.value,
                attempt_count=1,
                policy_version=policy_version,
                started_at=datetime.now(UTC),
            )
            session.add(task)  # type: ignore[attr-defined]
            source.status = CollectionSourceStatus.BROWSER_REQUIRED.value
            await session.flush()  # type: ignore[attr-defined]
            await session.refresh(task)  # type: ignore[attr-defined]
            return _browser_task_from_model(task)


async def increment_browser_attempt(
    browser_task_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
) -> BrowserTaskData | None:
    """Increment attempt_count on the same row for a Temporal retry with a fresh session."""
    async with session_scope() as session:
        async with session.begin():
            row = await session.scalar(  # type: ignore[attr-defined]
                select(BrowserTask).where(
                    BrowserTask.browser_task_id == browser_task_id,
                    BrowserTask.task_id == task_id,
                    BrowserTask.task_run_id == task_run_id,
                    BrowserTask.owner_id == owner_id,
                )
            )
            if row is None:
                return None
            row.attempt_count = int(row.attempt_count or 0) + 1
            row.started_at = datetime.now(UTC)
            row.failure_code = None
            row.failure_message = None
            await session.flush()  # type: ignore[attr-defined]
            await session.refresh(row)  # type: ignore[attr-defined]
            return _browser_task_from_model(row)


async def complete_browser_task(
    *,
    browser_task_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    snapshot_id: str,
    source_id: str,
) -> BrowserTaskData | None:
    """Mark RUNNING -> COMPLETED with the browser snapshot, and move the source to FETCHED."""
    async with session_scope() as session:
        async with session.begin():
            row = await session.scalar(  # type: ignore[attr-defined]
                select(BrowserTask).where(
                    BrowserTask.browser_task_id == browser_task_id,
                    BrowserTask.task_id == task_id,
                    BrowserTask.task_run_id == task_run_id,
                    BrowserTask.owner_id == owner_id,
                )
            )
            if row is None:
                return None
            if row.status != BrowserTaskStatus.RUNNING.value:
                # Idempotent replay guard: a completed task stays completed.
                if row.status == BrowserTaskStatus.COMPLETED.value and row.snapshot_id:
                    return _browser_task_from_model(row)
                return _browser_task_from_model(row)
            row.status = BrowserTaskStatus.COMPLETED.value
            row.snapshot_id = snapshot_id
            row.completed_at = datetime.now(UTC)
            row.failure_code = None
            row.failure_message = None
            source = await session.scalar(  # type: ignore[attr-defined]
                select(CollectionSource).where(
                    CollectionSource.source_id == source_id,
                    CollectionSource.task_id == task_id,
                    CollectionSource.owner_id == owner_id,
                )
                .with_for_update()
            )
            if source is not None:
                source.snapshot_id = snapshot_id
                source.status = CollectionSourceStatus.FETCHED.value
            await session.flush()  # type: ignore[attr-defined]
            await session.refresh(row)  # type: ignore[attr-defined]
            return _browser_task_from_model(row)


async def fail_browser_task(
    *,
    browser_task_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    failure_code: str,
    failure_message: str,
    terminal: bool = True,
    mark_source_blocked: bool = False,
    source_id: str | None = None,
) -> BrowserTaskData | None:
    """Mark RUNNING -> FAILED (transient) or BLOCKED (terminal business).

    `terminal=True` sets status=BLOCKED and (when mark_source_blocked) moves the
    source to BLOCKED. `terminal=False` leaves status=FAILED so a later Temporal
    retry can re-claim with a fresh Chromium.
    """
    async with session_scope() as session:
        async with session.begin():
            row = await session.scalar(  # type: ignore[attr-defined]
                select(BrowserTask).where(
                    BrowserTask.browser_task_id == browser_task_id,
                    BrowserTask.task_id == task_id,
                    BrowserTask.task_run_id == task_run_id,
                    BrowserTask.owner_id == owner_id,
                )
            )
            if row is None:
                return None
            if row.status not in {BrowserTaskStatus.RUNNING.value, BrowserTaskStatus.FAILED.value}:
                return _browser_task_from_model(row)
            row.failure_code = failure_code[:64]
            row.failure_message = failure_message[:500]
            row.completed_at = datetime.now(UTC)
            if terminal:
                row.status = BrowserTaskStatus.BLOCKED.value
                if mark_source_blocked and source_id:
                    source = await session.scalar(  # type: ignore[attr-defined]
                        select(CollectionSource).where(
                            CollectionSource.source_id == source_id,
                            CollectionSource.task_id == task_id,
                            CollectionSource.owner_id == owner_id,
                        )
                    )
                    if source is not None:
                        source.status = CollectionSourceStatus.BLOCKED.value
                        source.failure_code = failure_code[:64]
                        source.failure_message = failure_message[:500]
            else:
                row.status = BrowserTaskStatus.FAILED.value
            await session.flush()  # type: ignore[attr-defined]
            await session.refresh(row)  # type: ignore[attr-defined]
            return _browser_task_from_model(row)


async def list_browser_tasks_for_run(
    task_id: str,
    task_run_id: str,
    owner_id: str,
) -> list[BrowserTaskSummary]:
    async with session_scope() as session:
        rows = list(
            await session.scalars(
                select(BrowserTask).where(
                    BrowserTask.task_id == task_id,
                    BrowserTask.task_run_id == task_run_id,
                    BrowserTask.owner_id == owner_id,
                )
            )
        )
        source_urls: dict[str, str] = {}
        if rows:
            source_ids = sorted({row.source_id for row in rows})
            sources = await session.scalars(
                select(CollectionSource).where(
                    CollectionSource.source_id.in_(source_ids),
                    CollectionSource.task_id == task_id,
                    CollectionSource.owner_id == owner_id,
                )
            )
            for src in sources:
                source_urls[src.source_id] = src.url
        summaries: list[BrowserTaskSummary] = []
        for row in rows:
            summaries.append(
                BrowserTaskSummary(
                    browser_task_id=row.browser_task_id,
                    source_id=row.source_id,
                    source_url=source_urls.get(row.source_id, ""),
                    status=BrowserTaskStatus(row.status),
                    attempt_count=int(row.attempt_count or 0),
                    snapshot_id=row.snapshot_id,
                    failure_code=row.failure_code,
                    failure_message=row.failure_message,
                    started_at=row.started_at,
                    completed_at=row.completed_at,
                )
            )
        return summaries