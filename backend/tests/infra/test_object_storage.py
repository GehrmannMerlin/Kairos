"""Storage adapter error-boundary contracts."""

from __future__ import annotations

import pytest
from app.infra.object_storage import MinioObjectStorage, StorageOperationError
from urllib3.exceptions import HTTPError


def _storage(client) -> MinioObjectStorage:
    storage = object.__new__(MinioObjectStorage)
    storage._bucket = "test-bucket"
    storage._client = client
    return storage


@pytest.mark.asyncio
async def test_put_maps_urllib3_transport_error_to_typed_storage_error() -> None:
    """Would fail if a production transport exception leaks past the storage boundary."""

    class BrokenClient:
        def put_object(self, *args, **kwargs) -> None:
            raise HTTPError("connection reset")

    with pytest.raises(StorageOperationError) as exc:
        await _storage(BrokenClient()).put("artifacts/test.csv", b"x")

    assert exc.value.operation == "put"


@pytest.mark.asyncio
async def test_head_maps_urllib3_transport_error_instead_of_calling_it_missing() -> None:
    """Would fail if export existence checks treated storage outages as absent objects."""

    class BrokenClient:
        def stat_object(self, *args, **kwargs) -> None:
            raise HTTPError("connection reset")

    with pytest.raises(StorageOperationError) as exc:
        await _storage(BrokenClient()).head("artifacts/test.csv")

    assert exc.value.operation == "head"


@pytest.mark.asyncio
async def test_put_does_not_reclassify_programming_error_as_storage_error() -> None:
    """Would fail if the adapter hides arbitrary implementation defects as storage outages."""

    class BrokenClient:
        def put_object(self, *args, **kwargs) -> None:
            raise ValueError("invalid local call")

    with pytest.raises(ValueError, match="invalid local call"):
        await _storage(BrokenClient()).put("artifacts/test.csv", b"x")
