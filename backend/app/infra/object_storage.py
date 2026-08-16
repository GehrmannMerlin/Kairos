"""Object storage abstraction.

The app depends on the ``ObjectStorage`` protocol, not on MinIO/S3 directly.
M-01 ships one adapter (MinIO) which is also S3-compatible for production use.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

import anyio
from minio import Minio
from minio.error import MinioException, S3Error
from urllib3.exceptions import HTTPError

from app.config import Settings


@dataclass(frozen=True)
class ObjectMetadata:
    key: str
    size: int
    content_type: str | None
    etag: str | None
    content_sha256: str


class StorageOperationError(RuntimeError):
    """A safe, typed failure from the object-storage adapter boundary."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"object storage {operation} failed")


_StorageResult = TypeVar("_StorageResult")
_STORAGE_BACKEND_ERRORS = (MinioException, HTTPError, OSError)
_MISSING_OBJECT_CODES = frozenset({"NoSuchKey", "NoSuchObject", "NoSuchVersion"})


async def _run_storage_operation(
    operation: str, fn: Callable[[], _StorageResult]
) -> _StorageResult:
    try:
        return await anyio.to_thread.run_sync(fn)
    except _STORAGE_BACKEND_ERRORS as exc:
        raise StorageOperationError(operation) from exc


class ObjectStorage(Protocol):
    """Minimal S3-compatible object storage surface used by M-01."""

    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> ObjectMetadata: ...
    async def get(self, key: str) -> bytes: ...
    async def exists(self, key: str) -> bool: ...
    async def head(self, key: str) -> ObjectMetadata | None: ...
    async def delete(self, key: str) -> None: ...
    async def ensure_bucket(self) -> None: ...


class MinioObjectStorage:
    """S3-compatible adapter backed by the ``minio`` client (blocking calls in threads)."""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._client = Minio(
            settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=settings.s3_secure,
        )

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    async def ensure_bucket(self) -> None:
        if not await _run_storage_operation(
            "ensure_bucket", lambda: self._client.bucket_exists(self._bucket)
        ):
            await _run_storage_operation(
                "ensure_bucket", lambda: self._client.make_bucket(self._bucket)
            )

    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> ObjectMetadata:
        stream = io.BytesIO(data)

        def _put() -> None:
            self._client.put_object(
                self._bucket, key, stream, length=len(data), content_type=content_type
            )

        await _run_storage_operation("put", _put)
        return ObjectMetadata(
            key=key,
            size=len(data),
            content_type=content_type,
            etag=None,
            content_sha256=self._sha256(data),
        )

    async def get(self, key: str) -> bytes:
        response: Any = await _run_storage_operation(
            "get", lambda: self._client.get_object(self._bucket, key)
        )
        try:
            return response.read()
        except _STORAGE_BACKEND_ERRORS as exc:
            raise StorageOperationError("get") from exc
        finally:
            response.close()
            response.release_conn()

    async def exists(self, key: str) -> bool:
        return await self.head(key) is not None

    async def head(self, key: str) -> ObjectMetadata | None:
        try:
            stat = await anyio.to_thread.run_sync(self._client.stat_object, self._bucket, key)
        except S3Error as exc:
            if exc.code in _MISSING_OBJECT_CODES:
                return None
            raise StorageOperationError("head") from exc
        except _STORAGE_BACKEND_ERRORS as exc:
            raise StorageOperationError("head") from exc
        return ObjectMetadata(
            key=key,
            size=stat.size or 0,
            content_type=stat.content_type,
            etag=stat.etag,
            content_sha256="",
        )

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            self._client.remove_object(self._bucket, key)

        try:
            await anyio.to_thread.run_sync(_delete)
        except S3Error as exc:
            if exc.code not in _MISSING_OBJECT_CODES:
                raise StorageOperationError("delete") from exc
        except _STORAGE_BACKEND_ERRORS as exc:
            raise StorageOperationError("delete") from exc


def create_object_storage(settings: Settings) -> MinioObjectStorage:
    return MinioObjectStorage(settings)
