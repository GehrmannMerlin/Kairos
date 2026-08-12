"""M-14 Evidence read-model repository：owner-safe 读取 PageSnapshot + FieldEvidence。

跨用户/跨任务访问一律 404（不泄漏存在性）。只读，不修改任何业务状态。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.auth.errors import NotFoundError
from app.domain.models import FieldEvidence, PageSnapshot


class EvidenceRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def snapshot_for_task(
        self, *, user_id: int, task_id: int, snapshot_id: int
    ) -> PageSnapshot:
        row = self._db.get(PageSnapshot, snapshot_id)
        if (
            row is None
            or row.user_id != user_id
            or (task_id and row.task_id != task_id)
        ):
            raise NotFoundError("资源不存在")
        return row

    def field_evidence_for_snapshot(
        self, *, user_id: int, snapshot_id: int
    ) -> list[FieldEvidence]:
        return list(
            self._db.scalars(
                select(FieldEvidence).where(
                    FieldEvidence.user_id == user_id,
                    FieldEvidence.snapshot_id == snapshot_id,
                )
            )
        )
