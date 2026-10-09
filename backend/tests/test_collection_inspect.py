from __future__ import annotations

from uuid import uuid4

import pytest
from app.agent.deps import KairosAgentDeps
from app.agent.tools import inspect_snapshot
from app.domain import (
    CollectionError,
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionSpecConfirm,
    InspectSnapshotResult,
)
from app.repositories import (
    confirm_collection_spec,
    create_task_run,
    get_collection_source_for_run,
    get_collection_spec,
    insert_task,
    persist_page_snapshot,
)
from app.storage import InMemoryObjectStore
from pydantic_ai import RunContext


@pytest.mark.asyncio
async def test_inspect_snapshot_returns_bounded_chunk_and_rejects_large_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    await insert_task(task_id, owner_id)
    await confirm_collection_spec(
        task_id,
        owner_id,
        CollectionSpecConfirm(
            goal="inspect",
            fields=[CollectionFieldSpec(name="title", type=CollectionFieldType.STRING, required=True)],
            seed_urls=["https://example.com/page"],
        ),
    )
    spec = await get_collection_spec(task_id, owner_id)
    assert spec is not None
    await create_task_run(task_id, owner_id, run_id, f"workflow-{uuid4().hex}", "inspect")
    source = await get_collection_source_for_run(
        task_id, run_id, owner_id, spec.spec_version_id, "https://example.com/page"
    )
    assert source is not None

    store = InMemoryObjectStore()
    await store.put_bytes("text-key", b"alpha " * 2000, "text/plain")
    await persist_page_snapshot(
        source_id=source.source_id,
        owner_id=owner_id,
        task_id=task_id,
        task_run_id=run_id,
        url=source.url,
        canonical_url=source.canonical_url,
        status_code=200,
        content_type="text/plain",
        title="Test",
        content_hash="hash-inspect",
        raw_storage_key="raw-key",
        text_storage_key="text-key",
        bytes_read=10,
        text_chars=12000,
        text_preview="alpha " * 100,
    )
    monkeypatch.setattr("app.agent.tools.get_object_store", lambda: store)

    ctx = RunContext[object](
        deps=KairosAgentDeps(
            user_id=owner_id,
            task_id=task_id,
            task_run_id=run_id,
            spec_version_id=spec.spec_version_id,
            model_config_id="test-model",
        ),
        model=None,
        usage=None,
        run_id=run_id,
    )
    source = await get_collection_source_for_run(
        task_id, run_id, owner_id, spec.spec_version_id, "https://example.com/page"
    )
    assert source is not None and source.snapshot_id is not None

    result = await inspect_snapshot(ctx, source.snapshot_id, offset=5, limit=6000)

    assert isinstance(result, InspectSnapshotResult)
    assert len(result.content) <= 6000
    assert result.offset == 5
    assert result.has_more is True
    with pytest.raises(CollectionError, match="SNAPSHOT_LIMIT"):
        await inspect_snapshot(ctx, source.snapshot_id, limit=6001)
