from __future__ import annotations

import pytest
from app.storage import InMemoryObjectStore, snapshot_object_keys


def test_snapshot_object_keys_are_deterministic_and_scoped() -> None:
    keys = snapshot_object_keys("owner-a", "task-a", "hash-a")

    assert keys.raw == "snapshots/owner-a/task-a/hash-a/raw.html"
    assert keys.text == "snapshots/owner-a/task-a/hash-a/text.txt"


@pytest.mark.asyncio
async def test_in_memory_object_store_exposes_only_bounded_object_operations() -> None:
    store = InMemoryObjectStore()
    await store.put_bytes("snapshots/test/text.txt", b"hello", "text/plain")

    assert await store.exists("snapshots/test/text.txt")
    assert await store.get_bytes("snapshots/test/text.txt") == b"hello"
