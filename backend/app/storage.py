from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings, get_settings


class ObjectStore(Protocol):
    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get_bytes(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class SnapshotObjectKeys:
    raw: str
    text: str
    screenshot: str | None = None


def snapshot_object_keys(owner_id: str, task_id: str, content_hash: str) -> SnapshotObjectKeys:
    prefix = f"snapshots/{owner_id}/{task_id}/{content_hash}"
    return SnapshotObjectKeys(raw=f"{prefix}/raw.html", text=f"{prefix}/text.txt")


def browser_snapshot_object_keys(owner_id: str, task_id: str, content_hash: str) -> SnapshotObjectKeys:
    """Deterministic MinIO keys for a Browser-rendered snapshot (phase4).

    Follows the existing snapshot convention but under a `browser/` namespace so
    HTTP and Browser provenance stay distinct. Screenshot PNG is stored here too;
    only the storage key (never the bytes) reaches PostgreSQL or Temporal.
    """
    prefix = f"browser/{owner_id}/{task_id}/{content_hash}"
    return SnapshotObjectKeys(
        raw=f"{prefix}/rendered.html",
        text=f"{prefix}/text.txt",
        screenshot=f"{prefix}/screenshot.png",
    )


class InMemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.objects.setdefault(key, bytes(data))
        self.content_types.setdefault(key, content_type)

    async def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    async def exists(self, key: str) -> bool:
        return key in self.objects


class MinioS3ObjectStore:
    def __init__(self, settings: Settings | None = None, client: object | None = None) -> None:
        settings = settings or get_settings()
        if client is None:
            if not settings.minio_access_key or not settings.minio_secret_key:
                raise RuntimeError("MinIO credentials are unavailable in the Worker environment")
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=settings.minio_endpoint,
                aws_access_key_id=settings.minio_access_key,
                aws_secret_access_key=settings.minio_secret_key.get_secret_value(),
            )
        self._client = client
        self._bucket = settings.minio_bucket

    def _ensure_bucket_sync(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception as exc:
            error = getattr(exc, "response", {})
            status_code = getattr(error.get("ResponseMetadata", {}), "get", lambda *_: None)("HTTPStatusCode")
            if status_code not in {403, 404}:
                raise
            try:
                self._client.create_bucket(Bucket=self._bucket)
            except Exception as create_exc:
                create_error = getattr(create_exc, "response", {})
                error_code = getattr(create_error.get("Error", {}), "get", lambda *_: None)("Code")
                if error_code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                    raise

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._ensure_bucket_sync)
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def get_bytes(self, key: str) -> bytes:
        response: Mapping[str, object] = await asyncio.to_thread(
            self._client.get_object,
            Bucket=self._bucket,
            Key=key,
        )
        body = response["Body"]
        return await asyncio.to_thread(body.read)

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(self._client.head_object, Bucket=self._bucket, Key=key)
        except Exception as exc:
            error = getattr(exc, "response", {})
            status_code = getattr(error.get("ResponseMetadata", {}), "get", lambda *_: None)("HTTPStatusCode")
            if status_code == 404:
                return False
            raise
        return True
