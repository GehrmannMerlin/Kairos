from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, or_, select

from app.collection import (
    ValidatedRecord,
    normalize_record_data,
    record_identity_key,
    stable_record_fingerprint,
)
from app.db import session_scope
from app.domain import (
    BrowserLimits,
    CollectionCompletionDecision,
    CollectionError,
    CollectionExecutionContext,
    CollectionFieldSpec,
    CollectionMode,
    CollectionProgress,
    CollectionSourceOrigin,
    CollectionSourceStatus,
    CollectionSourceSummary,
    CollectionSpecConfirm,
    CommitExtractionResult,
    CompletionResult,
    EventEnvelope,
    RecordStatus,
    SearchLimits,
    SearchRoundStatus,
    SearchRoundSummary,
    SearchSourceResult,
    SearchSourcesResult,
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
    SearchRound,
    Task,
    TaskRun,
    Workspace,
)
from app.search import NormalizedSearchResult
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
        scope_domains=list(row.scope_domains_json or []),
        search_limits=SearchLimits.model_validate(row.search_limits_json or {}),
        browser_limits=BrowserLimits.model_validate(row.browser_limits_json or {}),
        browser_policy_version=row.browser_policy_version or "browser-policy-v1",
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
        title=row.search_title,
        snippet=(row.search_snippet or "")[:500],
    )


def _search_round_summary_from_model(row: SearchRound) -> SearchRoundSummary:
    return SearchRoundSummary(
        search_round_id=row.search_round_id,
        task_run_id=row.task_run_id,
        round_number=row.round_number,
        query=row.query,
        query_hash=row.query_hash,
        provider=row.provider,
        requested_results=row.requested_results,
        returned_results=row.returned_results,
        accepted_results=row.accepted_results,
        new_sources=row.new_sources,
        passed_records_before=row.passed_records_before,
        passed_records_after=row.passed_records_after,
        new_passed_records=row.new_passed_records,
        status=SearchRoundStatus(row.status),
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


async def _count_canonical_passed(
    session: object,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
) -> int:
    result = await session.scalar(  # type: ignore[attr-defined]
        select(func.count(Record.record_id)).where(
            Record.task_id == task_id,
            Record.task_run_id == task_run_id,
            Record.owner_id == owner_id,
            Record.spec_version_id == spec_version_id,
            Record.status == RecordStatus.PASSED.value,
            Record.canonical_record_id.is_(None),
        )
    )
    return int(result or 0)


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
                scope_domains_json=list(confirm.scope_domains),
                search_limits_json=confirm.search_limits.model_dump(mode="json"),
                browser_limits_json=confirm.browser_limits.model_dump(mode="json"),
                browser_policy_version="browser-policy-v1",
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
                        origin=CollectionSourceOrigin.SEED.value,
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
            target_count=spec.target_count,
            scope_domains=list(spec.scope_domains_json or []),
            search_limits=SearchLimits.model_validate(spec.search_limits_json or {}),
            browser_limits=BrowserLimits.model_validate(spec.browser_limits_json or {}),
            browser_policy_version=spec.browser_policy_version or "browser-policy-v1",
        )


async def get_completed_search_round_by_hash(
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
    query_hash: str,
) -> SearchRound | None:
    async with session_scope() as session:
        return await session.scalar(
            select(SearchRound).where(
                SearchRound.task_id == task_id,
                SearchRound.task_run_id == task_run_id,
                SearchRound.owner_id == owner_id,
                SearchRound.spec_version_id == spec_version_id,
                SearchRound.query_hash == query_hash,
                SearchRound.status == SearchRoundStatus.COMPLETED.value,
            )
        )


async def create_search_round(
    *,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
    query: str,
    query_hash: str,
    provider: str,
    requested_results: int,
) -> SearchRound:
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
            spec = await session.scalar(
                select(CollectionSpecVersion).where(
                    CollectionSpecVersion.spec_version_id == spec_version_id,
                    CollectionSpecVersion.task_id == task_id,
                    CollectionSpecVersion.owner_id == owner_id,
                )
            )
            if run is None or spec is None:
                raise CollectionError("COLLECTION_SCOPE_NOT_FOUND", "collection run scope not found")
            previous_round = await session.scalar(
                select(func.max(SearchRound.round_number)).where(SearchRound.task_run_id == task_run_id)
            )
            round_row = SearchRound(
                search_round_id=f"search-round-{uuid4().hex}",
                owner_id=owner_id,
                task_id=task_id,
                task_run_id=task_run_id,
                spec_version_id=spec_version_id,
                round_number=int(previous_round or 0) + 1,
                query=query,
                query_hash=query_hash,
                provider=provider,
                requested_results=requested_results,
                passed_records_before=await _count_canonical_passed(
                    session, task_id, task_run_id, owner_id, spec_version_id
                ),
                status=SearchRoundStatus.RUNNING.value,
            )
            session.add(round_row)
            await session.flush()
        await session.refresh(round_row)
        return round_row


async def fail_search_round(search_round_id: str, task_id: str, task_run_id: str, owner_id: str) -> None:
    async with session_scope() as session:
        row = await session.scalar(
            select(SearchRound).where(
                SearchRound.search_round_id == search_round_id,
                SearchRound.task_id == task_id,
                SearchRound.task_run_id == task_run_id,
                SearchRound.owner_id == owner_id,
            )
        )
        if row is not None:
            row.status = SearchRoundStatus.FAILED.value
            row.completed_at = datetime.now(UTC)
            await session.commit()


async def persist_search_round_results(
    *,
    search_round_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
    results: Sequence[NormalizedSearchResult],
    returned_results: int,
) -> SearchSourcesResult:
    async with session_scope() as session:
        async with session.begin():
            round_row = await session.scalar(
                select(SearchRound)
                .where(
                    SearchRound.search_round_id == search_round_id,
                    SearchRound.task_id == task_id,
                    SearchRound.task_run_id == task_run_id,
                    SearchRound.owner_id == owner_id,
                    SearchRound.spec_version_id == spec_version_id,
                )
                .with_for_update()
            )
            spec = await session.scalar(
                select(CollectionSpecVersion).where(
                    CollectionSpecVersion.spec_version_id == spec_version_id,
                    CollectionSpecVersion.task_id == task_id,
                    CollectionSpecVersion.owner_id == owner_id,
                )
            )
            if round_row is None or spec is None:
                raise CollectionError("SEARCH_ROUND_NOT_FOUND", "search round not found")
            existing_rows = list(
                await session.scalars(
                    select(CollectionSource)
                    .where(
                        CollectionSource.task_id == task_id,
                        CollectionSource.owner_id == owner_id,
                        CollectionSource.spec_version_id == spec_version_id,
                    )
                    .with_for_update()
                )
            )
            by_canonical = {row.canonical_url: row for row in existing_rows}
            source_results: list[SearchSourceResult] = []
            result_source_ids: list[str] = []
            new_sources = 0
            discovered_sources = sum(
                row.origin == CollectionSourceOrigin.SEARCH.value for row in existing_rows
            )
            max_new_sources = max(
                spec.search_limits_json.get("max_discovered_sources", 50) - discovered_sources,
                0,
            )
            seen: set[str] = set()
            for result in results:
                if result.url in seen:
                    continue
                seen.add(result.url)
                source = by_canonical.get(result.url)
                if source is None:
                    if new_sources >= max_new_sources:
                        continue
                    source = CollectionSource(
                        source_id=f"source-{uuid4().hex}",
                        owner_id=owner_id,
                        task_id=task_id,
                        spec_version_id=spec_version_id,
                        url=result.url,
                        canonical_url=result.url,
                        origin=CollectionSourceOrigin.SEARCH.value,
                        search_round_id=search_round_id,
                        discovered_query=round_row.query,
                        provider_rank=result.rank,
                        provider_score=result.provider_score,
                        search_title=result.title[:1000],
                        search_snippet=result.snippet[:500],
                        status=CollectionSourceStatus.PENDING.value,
                    )
                    session.add(source)
                    await session.flush()
                    by_canonical[result.url] = source
                    new_sources += 1
                elif source.search_title is None and result.title:
                    source.search_title = result.title[:1000]
                    if not source.search_snippet and result.snippet:
                        source.search_snippet = result.snippet[:500]
                result_source_ids.append(source.source_id)
                source_results.append(
                    SearchSourceResult(
                        source_id=source.source_id,
                        url=source.url,
                        title=result.title[:1000],
                        snippet=result.snippet[:500],
                        rank=result.rank,
                    )
                )
            round_row.returned_results = returned_results
            round_row.accepted_results = len(source_results)
            round_row.new_sources = new_sources
            round_row.result_source_ids_json = result_source_ids
            if new_sources == 0:
                round_row.status = SearchRoundStatus.COMPLETED.value
                round_row.completed_at = datetime.now(UTC)
                round_row.passed_records_after = round_row.passed_records_before
                round_row.new_passed_records = 0
            await session.flush()
            return SearchSourcesResult(
                search_round_id=round_row.search_round_id,
                query=round_row.query,
                returned_results=returned_results,
                new_sources_count=new_sources,
                sources=source_results[: round_row.requested_results],
            )


async def get_search_sources_result(
    search_round_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
) -> SearchSourcesResult | None:
    async with session_scope() as session:
        round_row = await session.scalar(
            select(SearchRound).where(
                SearchRound.search_round_id == search_round_id,
                SearchRound.task_id == task_id,
                SearchRound.task_run_id == task_run_id,
                SearchRound.owner_id == owner_id,
                SearchRound.spec_version_id == spec_version_id,
            )
        )
        if round_row is None:
            return None
        result_source_ids = list(round_row.result_source_ids_json or [])
        if not result_source_ids:
            return SearchSourcesResult(
                search_round_id=round_row.search_round_id,
                query=round_row.query,
                returned_results=round_row.returned_results,
                new_sources_count=round_row.new_sources,
                sources=[],
            )
        rows = await session.scalars(
            select(CollectionSource).where(
                CollectionSource.source_id.in_(result_source_ids),
                CollectionSource.task_id == task_id,
                CollectionSource.owner_id == owner_id,
                CollectionSource.spec_version_id == spec_version_id,
            )
        )
        sources_by_id = {row.source_id: row for row in rows}
        return SearchSourcesResult(
            search_round_id=round_row.search_round_id,
            query=round_row.query,
            returned_results=round_row.returned_results,
            new_sources_count=round_row.new_sources,
            sources=[
                SearchSourceResult(
                    source_id=sources_by_id[source_id].source_id,
                    url=sources_by_id[source_id].url,
                    title=(sources_by_id[source_id].search_title or "")[:1000],
                    snippet=(sources_by_id[source_id].search_snippet or "")[:500],
                    rank=max(sources_by_id[source_id].provider_rank or 1, 1),
                )
                for source_id in result_source_ids
                if source_id in sources_by_id
            ],
        )


async def list_search_rounds(
    task_id: str, task_run_id: str, owner_id: str, spec_version_id: str
) -> list[SearchRound]:
    async with session_scope() as session:
        rows = await session.scalars(
            select(SearchRound)
            .where(
                SearchRound.task_id == task_id,
                SearchRound.task_run_id == task_run_id,
                SearchRound.owner_id == owner_id,
                SearchRound.spec_version_id == spec_version_id,
            )
            .order_by(SearchRound.round_number)
        )
        return list(rows)


async def complete_search_round_if_terminal(
    search_round_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
) -> None:
    async with session_scope() as session:
        async with session.begin():
            await _complete_search_round_if_terminal_in_session(
                session, search_round_id, task_id, task_run_id, owner_id, spec_version_id
            )


async def _complete_search_round_if_terminal_in_session(
    session: object,
    search_round_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
) -> None:
    round_row = await session.scalar(  # type: ignore[attr-defined]
        select(SearchRound)
        .where(
            SearchRound.search_round_id == search_round_id,
            SearchRound.task_id == task_id,
            SearchRound.task_run_id == task_run_id,
            SearchRound.owner_id == owner_id,
            SearchRound.spec_version_id == spec_version_id,
        )
        .with_for_update()
    )
    if round_row is None or round_row.status != SearchRoundStatus.RUNNING.value:
        return
    sources = list(
        await session.scalars(  # type: ignore[attr-defined]
            select(CollectionSource).where(
                CollectionSource.search_round_id == search_round_id,
                CollectionSource.owner_id == owner_id,
            )
        )
    )
    terminal = {
        CollectionSourceStatus.PROCESSED.value,
        CollectionSourceStatus.FAILED.value,
        CollectionSourceStatus.BLOCKED.value,
        CollectionSourceStatus.SKIPPED.value,
    }
    if any(source.status not in terminal for source in sources):
        return
    passed_after = await _count_canonical_passed(session, task_id, task_run_id, owner_id, spec_version_id)
    round_row.passed_records_after = passed_after
    round_row.new_passed_records = max(passed_after - round_row.passed_records_before, 0)
    round_row.status = SearchRoundStatus.COMPLETED.value
    round_row.completed_at = datetime.now(UTC)


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
                        PageSnapshot.task_run_id == task_run_id,
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


async def mark_collection_source_attempt(
    source_id: str,
    task_id: str,
    task_run_id: str,
    owner_id: str,
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
            .with_for_update()
        )
        if source is None:
            raise CollectionError("SOURCE_NOT_FOUND", "collection source not found")
        source.last_attempt_at = datetime.now(UTC)
        source.attempt_count = int(source.attempt_count or 0) + 1
        await session.commit()


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
        if source.search_round_id is not None:
            await _complete_search_round_if_terminal_in_session(
                session,
                source.search_round_id,
                task_id,
                task_run_id,
                owner_id,
                source.spec_version_id,
            )
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
            spec = await session.scalar(
                select(CollectionSpecVersion).where(
                    CollectionSpecVersion.spec_version_id == spec_version_id,
                    CollectionSpecVersion.task_id == task_id,
                    CollectionSpecVersion.owner_id == owner_id,
                )
            )
            if spec is None:
                raise CollectionError("COLLECTION_SPEC_NOT_FOUND", "collection spec not found")
            spec_data = collection_spec_from_model(spec)

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
            new_canonical_records = 0
            duplicates = 0
            conflicts = 0
            for ordinal, validated in enumerate(validated_records):
                record_id = f"record-{uuid4().hex}"
                record_ids.append(record_id)
                normalized_data_json = normalize_record_data(spec_data, validated.data_json)
                record_fingerprint = stable_record_fingerprint(normalized_data_json)
                identity_key = record_identity_key(spec_data, normalized_data_json)
                status = validated.status.value
                validation_issues = list(validated.validation_issues)
                canonical_record_id: str | None = None
                existing_exact = await session.scalar(
                    select(Record)
                    .where(
                        Record.task_id == task_id,
                        Record.task_run_id == task_run_id,
                        Record.owner_id == owner_id,
                        Record.spec_version_id == spec_version_id,
                        Record.record_fingerprint == record_fingerprint,
                    )
                    .order_by(Record.created_at, Record.record_id)
                )
                existing_identity = await session.scalars(
                    select(Record)
                    .where(
                        Record.task_id == task_id,
                        Record.task_run_id == task_run_id,
                        Record.owner_id == owner_id,
                        Record.spec_version_id == spec_version_id,
                        Record.identity_key == identity_key,
                    )
                    .order_by(Record.created_at, Record.record_id)
                )
                identity_rows = list(existing_identity)
                canonical = next(
                    (row for row in identity_rows if row.canonical_record_id is None),
                    existing_exact,
                )
                if existing_exact is not None:
                    duplicates += 1
                    canonical_record_id = existing_exact.canonical_record_id or existing_exact.record_id
                elif canonical is not None:
                    duplicates += 1
                    canonical_record_id = canonical.canonical_record_id or canonical.record_id
                    if canonical.normalized_data_json != normalized_data_json:
                        conflicts += 1
                        issue = "CONFLICTING_SOURCE_VALUE"
                        if issue not in validation_issues:
                            validation_issues.append(issue)
                        status = RecordStatus.NEEDS_REVIEW.value
                        if issue not in canonical.validation_issues:
                            canonical.validation_issues = [*canonical.validation_issues, issue]
                        canonical.status = RecordStatus.NEEDS_REVIEW.value
                if canonical_record_id is None:
                    new_canonical_records += 1
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
                        normalized_data_json=normalized_data_json,
                        record_fingerprint=record_fingerprint,
                        identity_key=identity_key,
                        canonical_record_id=canonical_record_id,
                        status=status,
                        validation_issues=validation_issues,
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
                if source.search_round_id is not None:
                    await _complete_search_round_if_terminal_in_session(
                        session,
                        source.search_round_id,
                        task_id,
                        task_run_id,
                        owner_id,
                        spec_version_id,
                    )
            await session.flush()
            return CommitExtractionResult(
                extraction_commit_id=commit.extraction_commit_id,
                snapshot_id=snapshot_id,
                record_ids=record_ids,
                record_count=len(record_ids),
                idempotent=False,
                new_canonical_records=new_canonical_records,
                duplicates=duplicates,
                conflicts=conflicts,
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
        rounds = list(
            await session.scalars(
                select(SearchRound)
                .where(
                    SearchRound.task_id == task_id,
                    SearchRound.task_run_id == task_run_id,
                    SearchRound.owner_id == owner_id,
                    SearchRound.spec_version_id == spec_version_id,
                )
                .order_by(SearchRound.round_number)
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
        passed_canonical = sum(
            record.status == RecordStatus.PASSED.value and record.canonical_record_id is None
            for record in records
        )
        completed_rounds = [row for row in rounds if row.status == SearchRoundStatus.COMPLETED.value]
        discovered_sources = sum(source.origin == CollectionSourceOrigin.SEARCH.value for source in sources)
        saturation = (
            "SATURATED"
            if len(completed_rounds) >= 2
            and all(row.new_passed_records == 0 for row in completed_rounds[-2:])
            else "NOT_REACHED"
        )
        remaining = (
            source_counts[CollectionSourceStatus.PENDING] + source_counts[CollectionSourceStatus.FETCHED]
        )
        return CollectionProgress(
            total_sources=len(sources),
            pending_sources=source_counts[CollectionSourceStatus.PENDING],
            fetched_sources=source_counts[CollectionSourceStatus.FETCHED],
            processed_sources=source_counts[CollectionSourceStatus.PROCESSED],
            failed_sources=source_counts[CollectionSourceStatus.FAILED],
            blocked_sources=source_counts[CollectionSourceStatus.BLOCKED],
            skipped_sources=source_counts[CollectionSourceStatus.SKIPPED],
            total_records=len(records),
            passed_records=passed_canonical,
            needs_review_records=record_counts[RecordStatus.NEEDS_REVIEW],
            rejected_records=record_counts[RecordStatus.REJECTED],
            remaining_sources=remaining,
            mode=CollectionMode(spec.mode),
            target_count=spec.target_count,
            passed_canonical_records=passed_canonical,
            observations_total=len(records),
            canonical_records_total=sum(record.canonical_record_id is None for record in records),
            remaining_to_target=(
                max(spec.target_count - passed_canonical, 0) if spec.target_count is not None else None
            ),
            search_rounds_completed=len(completed_rounds),
            max_search_rounds=SearchLimits.model_validate(spec.search_limits_json or {}).max_search_rounds,
            sources_discovered=discovered_sources,
            new_sources_last_round=rounds[-1].new_sources if rounds else 0,
            last_round_new_passed_records=rounds[-1].new_passed_records if rounds else 0,
            saturation_state=saturation,
            actionable_sources=[
                collection_source_summary_from_model(source)
                for source in sources
                if source.status
                in {CollectionSourceStatus.PENDING.value, CollectionSourceStatus.FETCHED.value}
            ][:10],
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
        if source.search_round_id is not None:
            await _complete_search_round_if_terminal_in_session(
                session,
                source.search_round_id,
                task_id,
                task_run_id,
                owner_id,
                source.spec_version_id,
            )
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
                select(Task).where(Task.task_id == task_id, Task.owner_id == owner_id).with_for_update()
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
                source.status in {CollectionSourceStatus.FAILED.value, CollectionSourceStatus.BLOCKED.value}
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
                decision=run_status.value,
                reason="SPECIFIED_SOURCE_TERMINAL_STATE",
            )


async def apply_collection_completion_decision(
    task_id: str,
    *,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
    decision: CollectionCompletionDecision,
    final_answer: str | None = None,
) -> CompletionResult:
    decision_name = decision.decision
    if decision_name == "CONTINUE":
        raise CollectionError("COLLECTION_NOT_COMPLETE", "a CONTINUE decision cannot finalize a collection")
    if decision_name not in {"COMPLETED", "PARTIALLY_COMPLETED", "FAILED"}:
        raise CollectionError("INVALID_COMPLETION_DECISION", "unknown collection completion decision")
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
                select(Task).where(Task.task_id == task_id, Task.owner_id == owner_id).with_for_update()
            )
            spec = await session.scalar(
                select(CollectionSpecVersion).where(
                    CollectionSpecVersion.spec_version_id == spec_version_id,
                    CollectionSpecVersion.task_id == task_id,
                    CollectionSpecVersion.owner_id == owner_id,
                )
            )
            if run is None or task is None or spec is None:
                raise CollectionError("COLLECTION_SCOPE_NOT_FOUND", "collection run scope not found")
            sources = list(
                await session.scalars(
                    select(CollectionSource)
                    .where(
                        CollectionSource.task_id == task_id,
                        CollectionSource.owner_id == owner_id,
                        CollectionSource.spec_version_id == spec_version_id,
                    )
                    .with_for_update()
                )
            )
            if decision_name == "COMPLETED" and spec.mode in {
                CollectionMode.EXPLORATORY.value,
                CollectionMode.HYBRID.value,
            }:
                for source in sources:
                    if source.origin == CollectionSourceOrigin.SEARCH.value and source.status in {
                        CollectionSourceStatus.PENDING.value,
                        CollectionSourceStatus.FETCHED.value,
                    }:
                        source.status = CollectionSourceStatus.SKIPPED.value
                        source.failure_code = "TARGET_REACHED"
                        source.failure_message = (
                            "target count reached before this discovered source was processed"
                        )
                        source.processed_at = datetime.now(UTC)
                        if source.search_round_id is not None:
                            await _complete_search_round_if_terminal_in_session(
                                session,
                                source.search_round_id,
                                task_id,
                                task_run_id,
                                owner_id,
                                spec_version_id,
                            )
            remaining = sum(
                source.status in {CollectionSourceStatus.PENDING.value, CollectionSourceStatus.FETCHED.value}
                for source in sources
            )
            status = TaskRunStatus(decision_name)
            run.status = transition_task_run_status(TaskRunStatus(run.status), status).value
            task_status = TaskStatus(decision_name)
            task.status = transition_task_status(TaskStatus(task.status), task_status).value
            run.error_message = decision.reason if decision_name == "FAILED" else None
            run.final_answer = final_answer[:20_000] if final_answer is not None else None
            return CompletionResult(
                task_run_id=task_run_id,
                task_status=task_status,
                task_run_status=status,
                remaining_sources=remaining,
                summary=f"collection finished with {decision.reason.lower()}",
                decision=decision_name,
                reason=decision.reason,
                passed_records=decision.passed_records,
                target_count=decision.target_count,
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
    task_id: str,
    task_run_id: str,
    owner_id: str,
    spec_version_id: str,
    *,
    include_duplicates: bool = False,
) -> list[Record]:
    async with session_scope() as session:
        query = select(Record).where(
            Record.task_id == task_id,
            Record.task_run_id == task_run_id,
            Record.owner_id == owner_id,
            Record.spec_version_id == spec_version_id,
        )
        if not include_duplicates:
            query = query.where(Record.canonical_record_id.is_(None))
        rows = await session.scalars(query.order_by(Record.created_at, Record.ordinal, Record.record_id))
        return list(rows)


async def list_record_evidence(record_id: str, task_id: str, owner_id: str) -> list[FieldEvidence]:
    async with session_scope() as session:
        rows = await session.scalars(
            select(FieldEvidence)
            .join(Record, Record.record_id == FieldEvidence.record_id)
            .where(
                or_(FieldEvidence.record_id == record_id, Record.canonical_record_id == record_id),
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


async def has_agent_event(
    task_id: str,
    task_run_id: str,
    owner_id: str,
    event_type: str,
) -> bool:
    async with session_scope() as session:
        row = await session.scalar(
            select(AgentEvent.event_id).where(
                AgentEvent.task_id == task_id,
                AgentEvent.task_run_id == task_run_id,
                AgentEvent.owner_id == owner_id,
                AgentEvent.event_type == event_type,
            )
        )
        return row is not None


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
