"""Phase 4 BrowserTask repository: owner-scoped claim / complete / fail / idempotency.

These are integration tests against the local PostgreSQL (migration 0004 applied).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.browser.repository import (
    claim_browser_task,
    complete_browser_task,
    fail_browser_task,
    get_browser_task_for_source,
    list_browser_tasks_for_run,
)
from app.domain import (
    BrowserTaskStatus,
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionSourceStatus,
    CollectionSpecConfirm,
)
from app.repositories import confirm_collection_spec, create_task_run, insert_task


async def _seed_spec_source(
    *,
    with_snapshot: bool = False,
) -> tuple[str, str, str, str, str, str | None]:
    """Create a task with a confirmed spec and one seed source; return ids.

    When `with_snapshot=True`, also persist a real HTTP PageSnapshot row so a
    BrowserTask `complete_browser_task` can reference a valid snapshot_id FK.
    """
    owner = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    task_run_id = f"run-{uuid4().hex}"
    workflow_id = f"wf-{uuid4().hex}"
    await insert_task(task_id, owner)
    spec = await confirm_collection_spec(
        task_id,
        owner,
        CollectionSpecConfirm(
            goal="collect",
            fields=[
                CollectionFieldSpec(name="quote", type=CollectionFieldType.STRING, required=True),
            ],
            seed_urls=["https://quotes.toscrape.com/js/"],
            mode="SPECIFIED_SOURCE",
        ),
    )
    from app.db import SessionFactory
    from app.models import CollectionSource
    from app.repositories import persist_page_snapshot
    from sqlalchemy import select

    async with SessionFactory() as db:
        source_row = await db.scalar(
            select(CollectionSource).where(
                CollectionSource.task_id == task_id,
                CollectionSource.owner_id == owner,
                CollectionSource.canonical_url == "https://quotes.toscrape.com/js/",
            )
        )
        source_id = source_row.source_id if source_row else "source-missing"
    created = await create_task_run(task_id, owner, task_run_id, workflow_id, "collect")
    assert created is not None
    snapshot_id: str | None = None
    if with_snapshot:
        snap = await persist_page_snapshot(
            source_id=source_id,
            owner_id=owner,
            task_id=task_id,
            task_run_id=task_run_id,
            url="https://quotes.toscrape.com/js/",
            canonical_url="https://quotes.toscrape.com/js/",
            status_code=200,
            content_type="text/html",
            title="Quotes to Scrape (JS)",
            content_hash=f"hash-{uuid4().hex}",
            raw_storage_key=f"snapshots/{owner}/{task_id}/hash/raw.html",
            text_storage_key=f"snapshots/{owner}/{task_id}/hash/text.txt",
            bytes_read=10,
            text_chars=10,
            text_preview="html",
        )
        snapshot_id = snap.snapshot_id
    return owner, task_id, task_run_id, spec.spec_version_id, source_id, snapshot_id


@pytest.mark.asyncio
async def test_claim_creates_running_task_and_browser_required_source() -> None:
    owner, task_id, task_run_id, spec_version_id, source_id, _snap = await _seed_spec_source()
    claimed = await claim_browser_task(
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        spec_version_id=spec_version_id,
        source_id=source_id,
        policy_version="browser-policy-v1",
    )
    assert claimed is not None
    assert claimed.status is BrowserTaskStatus.RUNNING
    assert claimed.attempt_count == 1
    assert claimed.snapshot_id is None

    # Source moved to BROWSER_REQUIRED by claim.
    from app.db import SessionFactory
    from app.models import CollectionSource
    from sqlalchemy import select

    async with SessionFactory() as db:
        source = await db.scalar(
            select(CollectionSource).where(CollectionSource.source_id == source_id)
        )
        assert source is not None
        assert source.status == CollectionSourceStatus.BROWSER_REQUIRED.value


@pytest.mark.asyncio
async def test_concurrent_claim_returns_none_for_running() -> None:
    owner, task_id, task_run_id, spec_version_id, source_id, _snap = await _seed_spec_source()
    first = await claim_browser_task(
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        spec_version_id=spec_version_id,
        source_id=source_id,
        policy_version="browser-policy-v1",
    )
    assert first is not None
    second = await claim_browser_task(
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        spec_version_id=spec_version_id,
        source_id=source_id,
        policy_version="browser-policy-v1",
    )
    assert second is None  # BROWSER_TASK_ALREADY_RUNNING signal


@pytest.mark.asyncio
async def test_complete_is_idempotent_and_updates_source() -> None:
    owner, task_id, task_run_id, spec_version_id, source_id, snap = await _seed_spec_source(
        with_snapshot=True
    )
    assert snap is not None
    claimed = await claim_browser_task(
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        spec_version_id=spec_version_id,
        source_id=source_id,
        policy_version="browser-policy-v1",
    )
    assert claimed is not None
    snapshot_id = snap
    done = await complete_browser_task(
        browser_task_id=claimed.browser_task_id,
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        snapshot_id=snapshot_id,
        source_id=source_id,
    )
    assert done is not None
    assert done.status is BrowserTaskStatus.COMPLETED
    assert done.snapshot_id == snapshot_id

    # Repeat complete is a no-op (idempotent).
    done_again = await complete_browser_task(
        browser_task_id=claimed.browser_task_id,
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        snapshot_id=snapshot_id,
        source_id=source_id,
    )
    assert done_again is not None
    assert done_again.status is BrowserTaskStatus.COMPLETED

    # Source moved back to FETCHED with the browser snapshot.
    from app.db import SessionFactory
    from app.models import CollectionSource
    from sqlalchemy import select

    async with SessionFactory() as db:
        source = await db.scalar(
            select(CollectionSource).where(CollectionSource.source_id == source_id)
        )
        assert source is not None
        assert source.status == CollectionSourceStatus.FETCHED.value
        assert source.snapshot_id == snapshot_id


@pytest.mark.asyncio
async def test_completed_task_returns_existing_on_reclaim() -> None:
    owner, task_id, task_run_id, spec_version_id, source_id, snap = await _seed_spec_source(
        with_snapshot=True
    )
    assert snap is not None
    claimed = await claim_browser_task(
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        spec_version_id=spec_version_id,
        source_id=source_id,
        policy_version="browser-policy-v1",
    )
    assert claimed is not None
    snapshot_id = snap
    await complete_browser_task(
        browser_task_id=claimed.browser_task_id,
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        snapshot_id=snapshot_id,
        source_id=source_id,
    )
    # A fresh request for the same source returns the completed task, no new row.
    again = await claim_browser_task(
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        spec_version_id=spec_version_id,
        source_id=source_id,
        policy_version="browser-policy-v1",
    )
    assert again is not None
    assert again.browser_task_id == claimed.browser_task_id
    assert again.status is BrowserTaskStatus.COMPLETED
    assert again.snapshot_id == snapshot_id


@pytest.mark.asyncio
async def test_fail_blocked_is_terminal_and_marks_source_blocked() -> None:
    owner, task_id, task_run_id, spec_version_id, source_id, _snap = await _seed_spec_source()
    claimed = await claim_browser_task(
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        spec_version_id=spec_version_id,
        source_id=source_id,
        policy_version="browser-policy-v1",
    )
    assert claimed is not None
    failed = await fail_browser_task(
        browser_task_id=claimed.browser_task_id,
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        failure_code="CAPTCHA_REQUIRED",
        failure_message="captcha wall",
        terminal=True,
        mark_source_blocked=True,
        source_id=source_id,
    )
    assert failed is not None
    assert failed.status is BrowserTaskStatus.BLOCKED
    assert failed.failure_code == "CAPTCHA_REQUIRED"

    from app.db import SessionFactory
    from app.models import CollectionSource
    from sqlalchemy import select

    async with SessionFactory() as db:
        source = await db.scalar(
            select(CollectionSource).where(CollectionSource.source_id == source_id)
        )
        assert source is not None
        assert source.status == CollectionSourceStatus.BLOCKED.value


@pytest.mark.asyncio
async def test_fail_transient_allows_reclaim_with_increment() -> None:
    owner, task_id, task_run_id, spec_version_id, source_id, _snap = await _seed_spec_source()
    claimed = await claim_browser_task(
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        spec_version_id=spec_version_id,
        source_id=source_id,
        policy_version="browser-policy-v1",
    )
    assert claimed is not None
    failed = await fail_browser_task(
        browser_task_id=claimed.browser_task_id,
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        failure_code="TRANSIENT_RETRY",
        failure_message="navigation timeout",
        terminal=False,
    )
    assert failed is not None
    assert failed.status is BrowserTaskStatus.FAILED

    # A later Temporal attempt re-claims the SAME task row and bumps attempt_count.
    reclaimed = await claim_browser_task(
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        spec_version_id=spec_version_id,
        source_id=source_id,
        policy_version="browser-policy-v1",
    )
    assert reclaimed is not None
    assert reclaimed.browser_task_id == claimed.browser_task_id
    assert reclaimed.attempt_count == 2
    assert reclaimed.status is BrowserTaskStatus.RUNNING


@pytest.mark.asyncio
async def test_list_returns_summaries_with_urls() -> None:
    owner, task_id, task_run_id, spec_version_id, source_id, _snap = await _seed_spec_source()
    await claim_browser_task(
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        spec_version_id=spec_version_id,
        source_id=source_id,
        policy_version="browser-policy-v1",
    )
    rows = await list_browser_tasks_for_run(task_id, task_run_id, owner)
    assert len(rows) == 1
    assert rows[0].source_url == "https://quotes.toscrape.com/js/"
    assert rows[0].status is BrowserTaskStatus.RUNNING


@pytest.mark.asyncio
async def test_owner_isolation() -> None:
    owner, task_id, task_run_id, spec_version_id, source_id, _snap = await _seed_spec_source()
    await claim_browser_task(
        task_id=task_id,
        task_run_id=task_run_id,
        owner_id=owner,
        spec_version_id=spec_version_id,
        source_id=source_id,
        policy_version="browser-policy-v1",
    )
    # A different owner cannot see or reclaim the source's task.
    other = await get_browser_task_for_source(task_id, task_run_id, "other-owner", source_id)
    assert other is None
    other_rows = await list_browser_tasks_for_run(task_id, task_run_id, "other-owner")
    assert other_rows == []
