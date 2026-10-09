from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain import (
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionMode,
    CollectionProgress,
    CollectionSourceStatus,
    CollectionSpecConfirm,
    CompletionResult,
    TaskRunStatus,
    TaskStatus,
)
from app.repositories import (
    confirm_collection_spec,
    create_task_run,
    finalize_collection_run,
    get_collection_source_for_run,
    get_collection_spec,
    get_latest_task_run,
    get_task,
    insert_task,
    set_collection_source_status,
)


def test_collection_progress_counts_remaining_sources_from_persistence_shape() -> None:
    progress = CollectionProgress(
        total_sources=3,
        pending_sources=1,
        fetched_sources=1,
        processed_sources=1,
        failed_sources=0,
        blocked_sources=0,
        total_records=2,
        passed_records=1,
        needs_review_records=1,
        rejected_records=0,
        remaining_sources=2,
    )

    assert progress.remaining_sources == progress.pending_sources + progress.fetched_sources


def test_completion_result_keeps_partial_status_distinct_from_failed() -> None:
    result = CompletionResult(
        task_run_id="run-a",
        task_status=TaskStatus.PARTIALLY_COMPLETED,
        task_run_status=TaskRunStatus.PARTIALLY_COMPLETED,
        remaining_sources=0,
        summary="one source was blocked",
    )

    assert result.task_status.value == "PARTIALLY_COMPLETED"
    assert CollectionSourceStatus.BLOCKED.value == "BLOCKED"


async def _completion_scope() -> tuple[str, str, str, str, list[str]]:
    owner_id = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    await insert_task(task_id, owner_id)
    await confirm_collection_spec(
        task_id,
        owner_id,
        CollectionSpecConfirm(
            goal="complete",
            mode=CollectionMode.SPECIFIED_SOURCE,
            fields=[CollectionFieldSpec(name="title", type=CollectionFieldType.STRING, required=True)],
            seed_urls=[
                "https://example.com/one",
                "https://example.com/two",
                "https://example.com/three",
            ],
        ),
    )
    spec = await get_collection_spec(task_id, owner_id)
    assert spec is not None
    await create_task_run(task_id, owner_id, run_id, f"workflow-{uuid4().hex}", "complete")
    return owner_id, task_id, run_id, spec.spec_version_id, spec.seed_urls


@pytest.mark.asyncio
async def test_finalize_collection_run_marks_all_processed_as_completed() -> None:
    owner_id, task_id, run_id, spec_id, urls = await _completion_scope()
    for url in urls:
        source = await get_collection_source_for_run(task_id, run_id, owner_id, spec_id, url)
        assert source is not None
        await set_collection_source_status(
            source.source_id, task_id, run_id, owner_id, CollectionSourceStatus.PROCESSED
        )

    result = await finalize_collection_run(task_id, run_id, owner_id, spec_id)

    assert result.task_status is TaskStatus.COMPLETED
    assert result.task_run_status is TaskRunStatus.COMPLETED
    assert result.remaining_sources == 0
    task = await get_task(task_id, owner_id)
    run = await get_latest_task_run(task_id, owner_id)
    assert task is not None and task.status == TaskStatus.COMPLETED.value
    assert run is not None and run.status == TaskRunStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_finalize_collection_run_marks_terminal_blocked_scope_as_partial() -> None:
    owner_id, task_id, run_id, spec_id, urls = await _completion_scope()
    for index, url in enumerate(urls):
        source = await get_collection_source_for_run(task_id, run_id, owner_id, spec_id, url)
        assert source is not None
        await set_collection_source_status(
            source.source_id,
            task_id,
            run_id,
            owner_id,
            CollectionSourceStatus.BLOCKED if index == 0 else CollectionSourceStatus.PROCESSED,
        )

    result = await finalize_collection_run(task_id, run_id, owner_id, spec_id)

    assert result.task_status is TaskStatus.PARTIALLY_COMPLETED
    assert result.task_run_status is TaskRunStatus.PARTIALLY_COMPLETED
    assert result.remaining_sources == 0


@pytest.mark.asyncio
async def test_finalize_collection_run_rejects_early_agent_completion() -> None:
    owner_id, task_id, run_id, spec_id, urls = await _completion_scope()
    source = await get_collection_source_for_run(task_id, run_id, owner_id, spec_id, urls[0])
    assert source is not None
    await set_collection_source_status(
        source.source_id, task_id, run_id, owner_id, CollectionSourceStatus.PROCESSED
    )

    result = await finalize_collection_run(task_id, run_id, owner_id, spec_id)

    assert result.task_status is TaskStatus.FAILED
    assert result.task_run_status is TaskRunStatus.FAILED
    assert result.remaining_sources == 2
    run = await get_latest_task_run(task_id, owner_id)
    assert run is not None and run.error_message == "INCOMPLETE_COLLECTION"
