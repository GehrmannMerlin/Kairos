"""MinIO adapter round-trip against the live local MinIO."""

from __future__ import annotations

import pytest
from app.infra.deps import get_object_storage

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_put_get_exists_head_roundtrip() -> None:
    storage = get_object_storage()
    await storage.ensure_bucket()

    key = "test/roundtrip.txt"
    payload = b"kairos-object-storage-roundtrip"

    await storage.put(key, payload, content_type="text/plain")

    assert await storage.exists(key) is True
    assert await storage.get(key) == payload

    meta = await storage.head(key)
    assert meta is not None
    assert meta.size == len(payload)
    assert meta.content_type == "text/plain"

    assert await storage.exists("test/does-not-exist.txt") is False
    assert await storage.head("test/does-not-exist.txt") is None
