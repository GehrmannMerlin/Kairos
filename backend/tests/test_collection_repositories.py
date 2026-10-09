from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain import CollectionFieldSpec, CollectionFieldType, CollectionSpecConfirm
from app.repositories import confirm_collection_spec, insert_task


@pytest.mark.asyncio
async def test_confirm_collection_spec_creates_a_new_version_and_deduplicated_sources() -> None:
    task_id = f"task-{uuid4().hex}"
    await insert_task(task_id, "owner-test")
    result = await confirm_collection_spec(
        task_id,
        "owner-test",
        CollectionSpecConfirm(
            goal="extract",
            fields=[CollectionFieldSpec(name="title", type=CollectionFieldType.STRING, required=True)],
            seed_urls=["HTTPS://Example.com:443/path#one", "https://example.com/path#two"],
        ),
    )

    assert result.version == 1
    assert result.mode.value == "SPECIFIED_SOURCE"
    assert result.seed_urls == ["https://example.com/path"]
