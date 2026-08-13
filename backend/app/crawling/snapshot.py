"""PageSnapshot 持久化服务（M-10 / D-016 / 五十一~五十三）。

- 原始内容按 content hash 上传 ObjectStorage；DB 只存 metadata/hash/ref。
- 相同内容重抓：复用已有 Blob（content-addressable），仍保存新 observation 行 +
  snapshot_version 递增 + prior_snapshot_id 链（“何时再次抓取”审计事实）。
- 恢复幂等：对象先写（put）+ DB idempotent insert；重试时按
  (user_id, content_hash, tool) 或 storage.exists(key) 安全复用，不无限复制对象。
- immutable：历史 snapshot 绝不覆盖；本服务只追加。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from app.crawling.contracts import PageSnapshotRef
from app.crawling.repository import PageSnapshotRepository
from app.infra.object_storage import ObjectStorage


def _now() -> datetime:
    return datetime.now(UTC)


class PageSnapshotService:
    def __init__(
        self,
        db: Any,
        storage: ObjectStorage,
        *,
        user_id: int,
        task_id: int,
        run_id: int,
        spec_version: int,
        repository: PageSnapshotRepository | None = None,
    ) -> None:
        self._db = db
        self._storage = storage
        self._user_id = user_id
        self._task_id = task_id
        self._run_id = run_id
        self._spec_version = spec_version
        self._repo = repository or PageSnapshotRepository(db)

    @staticmethod
    def content_hash(body: bytes) -> str:
        return hashlib.sha256(body).hexdigest()

    def _key(self, content_hash: str, tool: str, ext: str) -> str:
        """owner-safe + content-addressable：用户前缀 + hash + 随机段。

        浏览器/用户不能通过猜 MinIO key 读取他人对象；所有 API 通过
        snapshot/evidence ID + ownership 访问（四十五）。
        """
        return f"snapshots/u{self._user_id}/{content_hash}/{tool}-{uuid.uuid4().hex}.{ext}"

    @staticmethod
    def _ext_for(mime_type: str | None) -> str:
        if not mime_type:
            return "bin"
        mime = mime_type.split(";")[0].strip().lower()
        return {
            "text/html": "html",
            "application/json": "json",
            "application/xml": "xml",
            "application/rss+xml": "rss",
            "application/atom+xml": "atom",
            "text/xml": "xml",
            "text/plain": "txt",
        }.get(mime, "bin")

    async def commit_raw(
        self,
        *,
        body: bytes,
        url_resource_id: int | None,
        tool: str,
        tool_version: str,
        source_url: str,
        final_url: str,
        http_status: int | None,
        content_type: str | None,
        content_length: int | None,
        duration_ms: int | None,
        redirect_summary: list[dict] | None = None,
        escalation_evidence: dict | None = None,
        credential_ref: dict | None = None,
        http_metadata: dict | None = None,
    ) -> PageSnapshotRef:
        """提交一次不可变抓取观察。幂等：同内容重试安全复用 Blob，不重复上传对象。"""
        digest = self.content_hash(body)
        mime_type = (content_type or "application/octet-stream").split(";")[0].strip()

        # 1. 同 (user, hash, tool) 已存在 → 复用其 storage_ref（D-016 内容哈希复用）
        existing = self._repo.find_latest_by_hash(
            user_id=self._user_id, content_hash=digest, tool=tool
        )
        if existing is not None and existing.storage_ref:
            storage_ref = existing.storage_ref
        else:
            key = self._key(digest, tool, self._ext_for(content_type))
            if not await self._storage.exists(key):
                await self._storage.put(
                    key, body, content_type=mime_type or "application/octet-stream"
                )
            storage_ref = key

        # 2. 新 observation 行：version 递增 + prior 指向上一 observation（同一 URL 链）
        version = (
            self._repo.next_version(
                user_id=self._user_id, url_resource_id=url_resource_id
            )
            if url_resource_id is not None
            else 1
        )
        prior = None
        if url_resource_id is not None and version > 1:
            prior_list = self._repo.find_by_url_resource(
                user_id=self._user_id, url_resource_id=url_resource_id
            )
            if prior_list:
                prior = prior_list[-1].id

        row = self._repo.create(
            user_id=self._user_id,
            task_id=self._task_id,
            run_id=self._run_id,
            url_resource_id=url_resource_id,
            spec_version=self._spec_version,
            content_hash=digest,
            storage_ref=storage_ref,
            mime_type=mime_type,
            tool=tool,
            tool_version=tool_version,
            final_url=final_url,
            http_status=http_status,
            content_length=content_length,
            download_bytes=len(body),
            duration_ms=duration_ms,
            redirect_summary=redirect_summary,
            escalation_evidence=escalation_evidence,
            snapshot_version=version,
            prior_snapshot_id=prior,
            credential_ref=credential_ref,
            http_metadata=http_metadata,
        )
        return PageSnapshotRef(
            snapshot_id=row.id,
            content_hash=digest,
            storage_ref=storage_ref,
            url=source_url or final_url,
            final_url=final_url,
            tool=tool,
            tool_version=tool_version,
            mime_type=mime_type,
            spec_version=self._spec_version,
            run_id=self._run_id,
            url_resource_id=url_resource_id,
            fetched_at=_now().isoformat(),
        )

    async def lookup_by_hash(self, *, content_hash: str, tool: str) -> PageSnapshotRef | None:
        existing = self._repo.find_latest_by_hash(
            user_id=self._user_id, content_hash=content_hash, tool=tool
        )
        if existing is None:
            return None
        return PageSnapshotRef(
            snapshot_id=existing.id,
            content_hash=existing.content_hash,
            storage_ref=existing.storage_ref or "",
            url=existing.final_url,
            final_url=existing.final_url,
            tool=existing.tool,
            tool_version=existing.tool_version,
            mime_type=existing.mime_type,
            spec_version=existing.spec_version,
            run_id=existing.run_id or self._run_id,
            url_resource_id=existing.url_resource_id,
            fetched_at=(existing.captured_at or _now()).isoformat(),
        )
