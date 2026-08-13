"""M-10 crawling repositories（owner-safe，D-023）。

PageSnapshot / SiteFetchStrategy 都通过显式 user_id 边界访问；跨用户访问
视为不存在（404），不泄漏存在性。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.auth.errors import NotFoundError
from app.domain.models import PageSnapshot, SiteFetchStrategy


class PageSnapshotRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def find_by_id(self, user_id: int, snapshot_id: int) -> PageSnapshot | None:
        row = self._db.get(PageSnapshot, snapshot_id)
        if row is None or row.user_id != user_id:
            return None
        return row

    def find_by_hash(self, *, user_id: int, content_hash: str, tool: str) -> list[PageSnapshot]:
        """同 (user, content_hash, tool) 已有 snapshot：用于 content-addressable Blob 复用。"""
        return list(
            self._db.scalars(
                select(PageSnapshot)
                .where(
                    PageSnapshot.user_id == user_id,
                    PageSnapshot.content_hash == content_hash,
                    PageSnapshot.tool == tool,
                )
                .order_by(PageSnapshot.id.asc())
            )
        )

    def find_latest_by_hash(
        self, *, user_id: int, content_hash: str, tool: str
    ) -> PageSnapshot | None:
        rows = self.find_by_hash(user_id=user_id, content_hash=content_hash, tool=tool)
        return rows[-1] if rows else None

    def next_version(self, *, user_id: int, url_resource_id: int) -> int:
        rows = list(
            self._db.scalars(
                select(PageSnapshot).where(
                    PageSnapshot.user_id == user_id,
                    PageSnapshot.url_resource_id == url_resource_id,
                )
            )
        )
        return (max(r.snapshot_version for r in rows) + 1) if rows else 1

    def create(
        self,
        *,
        user_id: int,
        task_id: int,
        run_id: int,
        url_resource_id: int | None,
        spec_version: int,
        content_hash: str,
        storage_ref: str,
        mime_type: str | None,
        tool: str,
        tool_version: str,
        final_url: str,
        http_status: int | None,
        content_length: int | None,
        download_bytes: int | None,
        duration_ms: int | None,
        redirect_summary: list[dict] | None,
        escalation_evidence: dict | None,
        snapshot_version: int,
        prior_snapshot_id: int | None,
        credential_ref: dict | None,
        http_metadata: dict | None,
    ) -> PageSnapshot:
        row = PageSnapshot(
            user_id=user_id,
            task_id=task_id,
            run_id=run_id,
            url_resource_id=url_resource_id,
            spec_version=spec_version,
            content_hash=content_hash,
            storage_ref=storage_ref,
            mime_type=mime_type,
            tool=tool,
            tool_version=tool_version,
            final_url=final_url,
            http_status=http_status,
            content_length=content_length,
            download_bytes=download_bytes,
            duration_ms=duration_ms,
            redirect_summary=redirect_summary,
            escalation_evidence=escalation_evidence,
            snapshot_version=snapshot_version,
            prior_snapshot_id=prior_snapshot_id,
            credential_ref=credential_ref,
            http_metadata=http_metadata,
            status="stored",
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def list_for_task(self, user_id: int, task_id: int, limit: int = 200) -> list[PageSnapshot]:
        return list(
            self._db.scalars(
                select(PageSnapshot)
                .where(PageSnapshot.user_id == user_id, PageSnapshot.task_id == task_id)
                .order_by(PageSnapshot.id.asc())
                .limit(limit)
            )
        )

    def find_by_url_resource(self, *, user_id: int, url_resource_id: int) -> list[PageSnapshot]:
        return list(
            self._db.scalars(
                select(PageSnapshot)
                .where(
                    PageSnapshot.user_id == user_id,
                    PageSnapshot.url_resource_id == url_resource_id,
                )
                .order_by(PageSnapshot.id.asc())
            )
        )


class SiteFetchStrategyRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def get(self, user_id: int, site_host: str) -> SiteFetchStrategy | None:
        return self._db.scalar(
            select(SiteFetchStrategy).where(
                SiteFetchStrategy.user_id == user_id,
                SiteFetchStrategy.site_host == site_host,
            )
        )

    def get_owned(self, user_id: int, strategy_id: int) -> SiteFetchStrategy:
        row = self._db.get(SiteFetchStrategy, strategy_id)
        if row is None or row.user_id != user_id:
            raise NotFoundError("资源不存在")
        return row

    def upsert(
        self,
        *,
        user_id: int,
        site_host: str,
        preferred_tier: str,
        tool: str,
        tool_version: str,
        structure_fingerprint: str | None,
        credential_required: bool,
        credential_type: str | None,
        last_success_at,
        expires_at,
        failure_count: int = 0,
        state: str = "valid",
    ) -> SiteFetchStrategy:
        row = self.get(user_id, site_host)
        if row is None:
            row = SiteFetchStrategy(user_id=user_id, site_host=site_host)
            self._db.add(row)
        row.preferred_tier = preferred_tier
        row.tool = tool
        row.tool_version = tool_version
        row.structure_fingerprint = structure_fingerprint
        row.credential_required = credential_required
        row.credential_type = credential_type
        row.last_success_at = last_success_at
        row.last_verified_at = last_success_at
        row.expires_at = expires_at
        row.failure_count = failure_count
        row.state = state
        self._db.commit()
        self._db.refresh(row)
        return row

    def invalidate(self, user_id: int, site_host: str) -> None:
        row = self.get(user_id, site_host)
        if row is None:
            return
        row.state = "expired"
        row.expires_at = None
        self._db.commit()

    def list_for_user(self, user_id: int) -> list[SiteFetchStrategy]:
        return list(
            self._db.scalars(
                select(SiteFetchStrategy)
                .where(SiteFetchStrategy.user_id == user_id)
                .order_by(SiteFetchStrategy.site_host.asc())
            )
        )
