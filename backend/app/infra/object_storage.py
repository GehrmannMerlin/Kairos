"""Object storage abstraction.

The app depends on the ``ObjectStorage`` protocol, not on MinIO/S3 directly.
M-01 ships one adapter (MinIO) which is also S3-compatible for production use.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
from dataclasses import dataclass
from typing import Protocol

import anyio
from minio import Minio

from app.config import Settings


@dataclass(frozen=True)
class ObjectMetadata:
    key: str
    size: int
    content_type: str | None
    etag: str | None
    content_sha256: str


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
        if not await anyio.to_thread.run_sync(self._client.bucket_exists, self._bucket):
            await anyio.to_thread.run_sync(self._client.make_bucket, self._bucket)

    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> ObjectMetadata:
        stream = io.BytesIO(data)

        def _put() -> None:
            self._client.put_object(
                self._bucket, key, stream, length=len(data), content_type=content_type
            )

        await anyio.to_thread.run_sync(_put)
        return ObjectMetadata(
            key=key,
            size=len(data),
            content_type=content_type,
            etag=None,
            content_sha256=self._sha256(data),
        )

    async def get(self, key: str) -> bytes:
        response = await anyio.to_thread.run_sync(self._client.get_object, self._bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    async def exists(self, key: str) -> bool:
        return await self.head(key) is not None

    async def head(self, key: str) -> ObjectMetadata | None:
        try:
            stat = await anyio.to_thread.run_sync(self._client.stat_object, self._bucket, key)
        except Exception:
            # Missing object (NoSuchKey / NoSuchObject) or permission error -> treat as absent.
            return None
        return ObjectMetadata(
            key=key,
            size=stat.size or 0,
            content_type=stat.content_type,
            etag=stat.etag,
            content_sha256="",
        )

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            with contextlib.suppress(Exception):
                # NoSuchKey / NoSuchObject 视为已删除，幂等。
                self._client.remove_object(self._bucket, key)

        await anyio.to_thread.run_sync(_delete)


def create_object_storage(settings: Settings) -> MinioObjectStorage:
    return MinioObjectStorage(settings)
