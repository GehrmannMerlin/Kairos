"""M-15 RetentionPolicy / CleanupResult / RetentionService（D-072）。

普通生命周期清理 ≠ permanent delete：只清理「到期 + 无保护引用」的重型 PageSnapshot 对象。
- 保护引用：FieldEvidence.snapshot_id → 该 snapshot 的 raw 对象不删（证据链仍在 DB）。
- FieldEvidence raw_snippet/source_locator 与对象解耦，天然长期保留。
- dry_run：只统计，不物理删除。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.models import FieldEvidence, PageSnapshot

logger = logging.getLogger(__name__)

POLICY_VERSION = "m15.1"


@dataclass
class CleanupResult:
    policy_version: str = POLICY_VERSION
    retention_days: int = 30
    dry_run: bool = False
    scanned: int = 0
    eligible: int = 0
    protected: int = 0
    deleted: int = 0
    failed: int = 0
    bytes_freed: int = 0
    started_at: str = ""
    completed_at: str = ""


@dataclass
class RetentionPolicy:
    retention_days: int = 30

    def is_expired(self, captured_at: datetime | None) -> bool:
        if captured_at is None:
            return False
        return captured_at < datetime.now(UTC) - timedelta(days=self.retention_days)


class RetentionService:
    def __init__(self, db, storage, *, retention_days: int) -> None:
        self._db = db
        self._storage = storage
        self._policy = RetentionPolicy(retention_days)

    async def run(self, *, dry_run: bool) -> CleanupResult:
        now_iso = datetime.now(UTC).isoformat()
        result = CleanupResult(
            dry_run=dry_run,
            retention_days=self._policy.retention_days,
            started_at=now_iso,
        )
        # 候选：有 storage_ref 的 PageSnapshot（重型 HTML/正文/截图/浏览器快照）
        candidates = list(
            self._db.scalars(select(PageSnapshot).where(PageSnapshot.storage_ref.is_not(None)))
        )
        # 保护集合：被 FieldEvidence.snapshot_id 引用的 snapshot id（证据链在 DB）
        protected_ids = set(
            self._db.scalars(
                select(FieldEvidence.snapshot_id).where(FieldEvidence.snapshot_id.is_not(None))
            ).all()
        )
        result.scanned = len(candidates)
        for snap in candidates:
            if not self._policy.is_expired(snap.captured_at):
                continue
            result.eligible += 1
            if snap.id in protected_ids:
                result.protected += 1
                continue
            if dry_run:
                continue
            try:
                freed = await self._remove_object(snap)
                result.deleted += 1
                result.bytes_freed += freed
            except Exception:  # noqa: BLE001 —— 单对象失败不中断整轮
                logger.warning(
                    "retention delete failed for snapshot %s", snap.id, exc_info=True
                )
                result.failed += 1
        result.completed_at = datetime.now(UTC).isoformat()
        self._db.commit()
        return result

    async def _remove_object(self, snap: PageSnapshot) -> int:
        ref = snap.storage_ref
        if not ref:
            return 0
        meta = await self._storage.head(ref)
        if meta is not None:
            await self._storage.delete(ref)
        snap.storage_ref = None
        snap.status = "retention_removed"
        self._db.add(snap)
        return meta.size if meta else 0
