from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select

from app.collection import ValidatedRecord
from app.db import session_scope
from app.domain import (
    CollectionError,
    CollectionExecutionContext,
    CollectionFieldSpec,
    CollectionMode,
    CollectionProgress,
    CollectionSourceStatus,
    CollectionSourceSummary,
    CollectionSpecConfirm,
    CommitExtractionResult,
    CompletionResult,
    EventEnvelope,
    RecordStatus,
    TaskRunStatus,
    TaskStatus,
    WorkspaceMetadata,
    WorkspacePermission,
    transition_task_run_status,
    transition_task_status,
)
from app.domain import CollectionSpecVersion as CollectionSpecVersionData
from app.models import (
    AgentEvent,
    CollectionSource,
    CollectionSpecVersion,
    ExtractionCommit,
    FieldEvidence,
    PageSnapshot,
    Record,
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


async def get_collection_source_for_run(
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
    canonical_url: str,
) -> CollectionSource | None:
    async with session_scope() as session:
        return await session.scalar(
            select(CollectionSource)
            .join(TaskRun, TaskRun.task_id == CollectionSource.task_id)
            .where(
                CollectionSource.task_id == task_id,
                CollectionSource.owner_id == owner_id,
                CollectionSource.spec_version_id == spec_version_id,
                CollectionSource.canonical_url == canonical_url,
                TaskRun.task_run_id == task_run_id,
                TaskRun.owner_id == owner_id,
            )
        )


async def get_snapshot_for_scope(
    snapshot_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
) -> PageSnapshot | None:
    async with session_scope() as session:
        return await session.scalar(
            select(PageSnapshot)
            .join(CollectionSource, CollectionSource.source_id == PageSnapshot.source_id)
            .where(
                PageSnapshot.snapshot_id == snapshot_id,
                PageSnapshot.task_id == task_id,
                PageSnapshot.task_run_id == task_run_id,
                PageSnapshot.owner_id == owner_id,
                CollectionSource.owner_id == owner_id,
                CollectionSource.spec_version_id == spec_version_id,
            )
        )


async def persist_page_snapshot(
    *,
    source_id: str,
    owner_id: str,
    task_id: str,
    task_run_id: str,
    url: str,
    canonical_url: str,
    status_code: int,
    content_type: str,
    title: str | None,
    content_hash: str,
    raw_storage_key: str,
    text_storage_key: str,
    bytes_read: int,
    text_chars: int,
    text_preview: str,
) -> PageSnapshot:
    async with session_scope() as session:
        async with session.begin():
            source = await session.scalar(
                select(CollectionSource)
                .join(TaskRun, TaskRun.task_id == CollectionSource.task_id)
                .where(
                    CollectionSource.source_id == source_id,
                    CollectionSource.owner_id == owner_id,
                    CollectionSource.task_id == task_id,
                    TaskRun.task_run_id == task_run_id,
                    TaskRun.owner_id == owner_id,
                )
                .with_for_update()
            )
            if source is None:
                raise CollectionError("SOURCE_NOT_FOUND", "collection source not found")
            if (
                source.status
                in {
                    CollectionSourceStatus.FETCHED.value,
                    CollectionSourceStatus.PROCESSED.value,
                }
                and source.snapshot_id
            ):
                existing = await session.scalar(
                    select(PageSnapshot).where(
                        PageSnapshot.snapshot_id == source.snapshot_id,
                        PageSnapshot.owner_id == owner_id,
                        PageSnapshot.task_id == task_id,
                    )
                )
                if existing is not None:
                    return existing

            snapshot = PageSnapshot(
                snapshot_id=f"snapshot-{uuid4().hex}",
                owner_id=owner_id,
                task_id=task_id,
                task_run_id=task_run_id,
                source_id=source_id,
                url=url,
                canonical_url=canonical_url,
                status_code=status_code,
                content_type=content_type,
                title=title,
                content_hash=content_hash,
                raw_storage_key=raw_storage_key,
                text_storage_key=text_storage_key,
                bytes_read=bytes_read,
                text_chars=text_chars,
                text_preview=text_preview[:2000],
            )
            session.add(snapshot)
            await session.flush()
            source.snapshot_id = snapshot.snapshot_id
            source.status = CollectionSourceStatus.FETCHED.value
        await session.refresh(snapshot)
        return snapshot


async def mark_collection_source_failure(
    source_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    status: CollectionSourceStatus,
    failure_code: str,
    failure_message: str,
) -> None:
    async with session_scope() as session:
        source = await session.scalar(
            select(CollectionSource)
            .join(TaskRun, TaskRun.task_id == CollectionSource.task_id)
            .where(
                CollectionSource.source_id == source_id,
                CollectionSource.task_id == task_id,
                CollectionSource.owner_id == owner_id,
                TaskRun.task_run_id == task_run_id,
                TaskRun.owner_id == owner_id,
            )
        )
        if source is None:
            return
        source.status = status.value
        source.failure_code = failure_code[:64]
        source.failure_message = failure_message[:500]
        await session.commit()


async def get_collection_spec_for_run(
    task_id: str, task_run_id: str, owner_id: str, spec_version_id: str
) -> CollectionSpecVersionData | None:
    async with session_scope() as session:
        row = await session.scalar(
            select(CollectionSpecVersion)
            .join(TaskRun, TaskRun.task_id == CollectionSpecVersion.task_id)
            .where(
                CollectionSpecVersion.spec_version_id == spec_version_id,
                CollectionSpecVersion.task_id == task_id,
                CollectionSpecVersion.owner_id == owner_id,
                TaskRun.task_run_id == task_run_id,
                TaskRun.owner_id == owner_id,
            )
        )
        return collection_spec_from_model(row) if row else None


async def get_extraction_commit_for_scope(
    snapshot_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
) -> ExtractionCommit | None:
    async with session_scope() as session:
        return await session.scalar(
            select(ExtractionCommit)
            .join(TaskRun, TaskRun.task_run_id == ExtractionCommit.task_run_id)
            .where(
                ExtractionCommit.snapshot_id == snapshot_id,
                ExtractionCommit.task_id == task_id,
                ExtractionCommit.task_run_id == task_run_id,
                ExtractionCommit.owner_id == owner_id,
                ExtractionCommit.spec_version_id == spec_version_id,
                TaskRun.task_id == task_id,
                TaskRun.owner_id == owner_id,
            )
        )


async def persist_extraction_commit(
    *,
    snapshot_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
    payload_hash: str,
    validated_records: Sequence[ValidatedRecord],
    source_complete: bool,
) -> CommitExtractionResult:
    async with session_scope() as session:
        async with session.begin():
            source = await session.scalar(
                select(CollectionSource)
                .join(TaskRun, TaskRun.task_id == CollectionSource.task_id)
                .where(
                    CollectionSource.task_id == task_id,
                    CollectionSource.owner_id == owner_id,
                    CollectionSource.spec_version_id == spec_version_id,
                    TaskRun.task_run_id == task_run_id,
                    TaskRun.owner_id == owner_id,
                    CollectionSource.snapshot_id == snapshot_id,
                )
                .with_for_update()
            )
            snapshot = await session.scalar(
                select(PageSnapshot).where(
                    PageSnapshot.snapshot_id == snapshot_id,
                    PageSnapshot.task_id == task_id,
                    PageSnapshot.task_run_id == task_run_id,
                    PageSnapshot.owner_id == owner_id,
                )
            )
            if source is None or snapshot is None:
                raise CollectionError("SNAPSHOT_NOT_FOUND", "snapshot not found")

            existing = await session.scalar(
                select(ExtractionCommit)
                .where(
                    ExtractionCommit.task_run_id == task_run_id,
                    ExtractionCommit.snapshot_id == snapshot_id,
                    ExtractionCommit.owner_id == owner_id,
                )
                .with_for_update()
            )
            if existing is not None:
                if existing.payload_hash != payload_hash:
                    raise CollectionError(
                        "ALREADY_COMMITTED_DIFFERENT_PAYLOAD",
                        "snapshot already has a different extraction payload",
                    )
                record_ids = list(
                    await session.scalars(
                        select(Record.record_id)
                        .where(
                            Record.extraction_commit_id == existing.extraction_commit_id,
                            Record.owner_id == owner_id,
                        )
                        .order_by(Record.ordinal)
                    )
                )
                return CommitExtractionResult(
                    extraction_commit_id=existing.extraction_commit_id,
                    snapshot_id=snapshot_id,
                    record_ids=record_ids,
                    record_count=existing.record_count,
                    idempotent=True,
                )

            commit = ExtractionCommit(
                extraction_commit_id=f"extract-{uuid4().hex}",
                owner_id=owner_id,
                task_id=task_id,
                task_run_id=task_run_id,
                spec_version_id=spec_version_id,
                snapshot_id=snapshot_id,
                payload_hash=payload_hash,
                record_count=len(validated_records),
            )
            session.add(commit)
            await session.flush()

            record_ids: list[str] = []
            for ordinal, validated in enumerate(validated_records):
                record_id = f"record-{uuid4().hex}"
                record_ids.append(record_id)
                session.add(
                    Record(
                        record_id=record_id,
                        owner_id=owner_id,
                        task_id=task_id,
                        task_run_id=task_run_id,
                        spec_version_id=spec_version_id,
                        snapshot_id=snapshot_id,
                        extraction_commit_id=commit.extraction_commit_id,
                        ordinal=ordinal,
                        data_json=validated.data_json,
                        status=validated.status.value,
                        validation_issues=validated.validation_issues,
                    )
                )
                await session.flush()
                for evidence in validated.evidence:
                    session.add(
                        FieldEvidence(
                            evidence_id=f"evidence-{uuid4().hex}",
                            record_id=record_id,
                            field_name=evidence.field_name,
                            snapshot_id=snapshot.snapshot_id,
                            source_url=snapshot.url,
                            quote=evidence.quote[:1000],
                            locator_type="TEXT_QUOTE",
                            locator_json={"verified": evidence.verified},
                            extraction_method="LLM_TYPED_EXTRACTION",
                            confidence=evidence.confidence,
                            verified=evidence.verified,
                        )
                    )
            if source_complete:
                source.status = CollectionSourceStatus.PROCESSED.value
                source.processed_at = datetime.now(UTC)
            await session.flush()
            return CommitExtractionResult(
                extraction_commit_id=commit.extraction_commit_id,
                snapshot_id=snapshot_id,
                record_ids=record_ids,
                record_count=len(record_ids),
                idempotent=False,
            )


async def get_collection_progress(
    task_id: str, task_run_id: str, owner_id: str, spec_version_id: str
) -> CollectionProgress:
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
            raise CollectionError("COLLECTION_SCOPE_NOT_FOUND", "collection run scope not found")

        sources = list(
            await session.scalars(
                select(CollectionSource)
                .where(
                    CollectionSource.task_id == task_id,
                    CollectionSource.owner_id == owner_id,
                    CollectionSource.spec_version_id == spec_version_id,
                )
                .order_by(CollectionSource.created_at, CollectionSource.source_id)
            )
        )
        records = list(
            await session.scalars(
                select(Record).where(
                    Record.task_id == task_id,
                    Record.task_run_id == task_run_id,
                    Record.owner_id == owner_id,
                    Record.spec_version_id == spec_version_id,
                )
            )
        )
        source_counts = {status: 0 for status in CollectionSourceStatus}
        for source in sources:
            try:
                source_counts[CollectionSourceStatus(source.status)] += 1
            except ValueError:
                continue
        record_counts = {status: 0 for status in RecordStatus}
        for record in records:
            try:
                record_counts[RecordStatus(record.status)] += 1
            except ValueError:
                continue
        remaining = (
            source_counts[CollectionSourceStatus.PENDING]
            + source_counts[CollectionSourceStatus.FETCHED]
        )
        return CollectionProgress(
            total_sources=len(sources),
            pending_sources=source_counts[CollectionSourceStatus.PENDING],
            fetched_sources=source_counts[CollectionSourceStatus.FETCHED],
            processed_sources=source_counts[CollectionSourceStatus.PROCESSED],
            failed_sources=source_counts[CollectionSourceStatus.FAILED],
            blocked_sources=source_counts[CollectionSourceStatus.BLOCKED],
            total_records=len(records),
            passed_records=record_counts[RecordStatus.PASSED],
            needs_review_records=record_counts[RecordStatus.NEEDS_REVIEW],
            rejected_records=record_counts[RecordStatus.REJECTED],
            remaining_sources=remaining,
        )


async def set_collection_source_status(
    source_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    status: CollectionSourceStatus,
) -> None:
    async with session_scope() as session:
        source = await session.scalar(
            select(CollectionSource)
            .join(TaskRun, TaskRun.task_id == CollectionSource.task_id)
            .where(
                CollectionSource.source_id == source_id,
                CollectionSource.task_id == task_id,
                CollectionSource.owner_id == owner_id,
                TaskRun.task_run_id == task_run_id,
                TaskRun.owner_id == owner_id,
            )
        )
        if source is None:
            raise CollectionError("SOURCE_NOT_FOUND", "collection source not found")
        source.status = status.value
        source.processed_at = datetime.now(UTC) if status is CollectionSourceStatus.PROCESSED else None
        await session.commit()


async def finalize_collection_run(
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
    final_answer: str | None = None,
) -> CompletionResult:
    async with session_scope() as session:
        async with session.begin():
            run = await session.scalar(
                select(TaskRun)
                .where(
                    TaskRun.task_run_id == task_run_id,
                    TaskRun.task_id == task_id,
                    TaskRun.owner_id == owner_id,
                )
                .with_for_update()
            )
            task = await session.scalar(
                select(Task)
                .where(Task.task_id == task_id, Task.owner_id == owner_id)
                .with_for_update()
            )
            sources = list(
                await session.scalars(
                    select(CollectionSource).where(
                        CollectionSource.task_id == task_id,
                        CollectionSource.owner_id == owner_id,
                        CollectionSource.spec_version_id == spec_version_id,
                    )
                )
            )
            if run is None or task is None:
                raise CollectionError("COLLECTION_SCOPE_NOT_FOUND", "collection run scope not found")
            if task.spec_version_id != spec_version_id:
                raise CollectionError("COLLECTION_SCOPE_NOT_FOUND", "collection spec is not current")

            remaining = sum(
                source.status
                in {
                    CollectionSourceStatus.PENDING.value,
                    CollectionSourceStatus.FETCHED.value,
                }
                for source in sources
            )
            has_failures = any(
                source.status
                in {CollectionSourceStatus.FAILED.value, CollectionSourceStatus.BLOCKED.value}
                for source in sources
            )
            if remaining:
                run_status = TaskRunStatus.FAILED
                task_status = TaskStatus.FAILED
                summary = "collection run ended before all sources reached a terminal state"
                run.error_message = "INCOMPLETE_COLLECTION"
            elif has_failures:
                run_status = TaskRunStatus.PARTIALLY_COMPLETED
                task_status = TaskStatus.PARTIALLY_COMPLETED
                summary = "collection completed with failed or blocked sources"
                run.error_message = None
            else:
                run_status = TaskRunStatus.COMPLETED
                task_status = TaskStatus.COMPLETED
                summary = "collection completed successfully"
                run.error_message = None
            run.status = transition_task_run_status(TaskRunStatus(run.status), run_status).value
            task.status = transition_task_status(TaskStatus(task.status), task_status).value
            run.final_answer = final_answer[:20_000] if final_answer is not None else None
            return CompletionResult(
                task_run_id=task_run_id,
                task_status=task_status,
                task_run_status=run_status,
                remaining_sources=remaining,
                summary=summary,
            )


async def list_collection_sources(
    task_id: str, owner_id: str, spec_version_id: str
) -> list[CollectionSource]:
    async with session_scope() as session:
        rows = await session.scalars(
            select(CollectionSource)
            .where(
                CollectionSource.task_id == task_id,
                CollectionSource.owner_id == owner_id,
                CollectionSource.spec_version_id == spec_version_id,
            )
            .order_by(CollectionSource.created_at, CollectionSource.source_id)
        )
        return list(rows)


async def list_collection_records(
    task_id: str, task_run_id: str, owner_id: str, spec_version_id: str
) -> list[Record]:
    async with session_scope() as session:
        rows = await session.scalars(
            select(Record)
            .where(
                Record.task_id == task_id,
                Record.task_run_id == task_run_id,
                Record.owner_id == owner_id,
                Record.spec_version_id == spec_version_id,
            )
            .order_by(Record.created_at, Record.ordinal, Record.record_id)
        )
        return list(rows)


async def list_record_evidence(record_id: str, task_id: str, owner_id: str) -> list[FieldEvidence]:
    async with session_scope() as session:
        rows = await session.scalars(
            select(FieldEvidence)
            .join(Record, Record.record_id == FieldEvidence.record_id)
            .where(
                FieldEvidence.record_id == record_id,
                Record.task_id == task_id,
                Record.owner_id == owner_id,
            )
            .order_by(FieldEvidence.created_at, FieldEvidence.evidence_id)
        )
        return list(rows)


async def get_collection_record(record_id: str, task_id: str, owner_id: str) -> Record | None:
    async with session_scope() as session:
        return await session.scalar(
            select(Record).where(
                Record.record_id == record_id,
                Record.task_id == task_id,
                Record.owner_id == owner_id,
            )
        )


async def get_snapshot_metadata(snapshot_id: str, task_id: str, owner_id: str) -> PageSnapshot | None:
    async with session_scope() as session:
        return await session.scalar(
            select(PageSnapshot)
            .join(CollectionSource, CollectionSource.source_id == PageSnapshot.source_id)
            .where(
                PageSnapshot.snapshot_id == snapshot_id,
                PageSnapshot.task_id == task_id,
                PageSnapshot.owner_id == owner_id,
                CollectionSource.task_id == task_id,
                CollectionSource.owner_id == owner_id,
            )
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
