"""M-14 Quality read-model repository：只读取 M-12 事实，不写任何业务状态。

所有查询强制 user_id + task_id 边界。记录字段值兼容真实提取（payload.values 嵌套）
与测试/旧数据（payload 平铺）两种形态。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.domain.models import (
    CollectionSpecVersion,
    FieldConflict,
    QualitySnapshot,
    Record,
    URLResource,
)
from app.validation.repository import ValidationRepository

_COVERED_STATUSES = ("FETCHED", "HANDED_OFF")


class QualityRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    # ---- QualitySnapshot（Metrics Version Boundary）----
    def latest_snapshot(self, *, user_id: int, task_id: int) -> QualitySnapshot | None:
        return ValidationRepository(self._db).latest_snapshot(user_id=user_id, task_id=task_id)

    # ---- Record partition / review_type 事实 ----
    def count_by_partition(self, *, user_id: int, task_id: int) -> dict[str, int]:
        rows = self._db.execute(
            select(Record.partition, func.count())
            .where(Record.user_id == user_id, Record.task_id == task_id)
            .group_by(Record.partition)
        ).all()
        return {p: int(c) for p, c in rows}

    def count_review_type(self, *, user_id: int, task_id: int, review_type: str) -> int:
        return int(
            self._db.scalar(
                select(func.count())
                .select_from(Record)
                .where(
                    Record.user_id == user_id,
                    Record.task_id == task_id,
                    Record.review_type == review_type,
                )
            )
            or 0
        )

    def unresolved_conflict_count(self, *, user_id: int, task_id: int) -> int:
        return int(
            self._db.scalar(
                select(func.count())
                .select_from(FieldConflict)
                .where(
                    FieldConflict.user_id == user_id,
                    FieldConflict.task_id == task_id,
                    FieldConflict.state == "unresolved",
                )
            )
            or 0
        )

    # ---- 字段完整性（CollectionSpec 字段 schema + Record facts）----
    def spec_for_task(self, *, user_id: int, task_id: int) -> CollectionSpecVersion | None:
        return self._db.scalar(
            select(CollectionSpecVersion)
            .where(
                CollectionSpecVersion.user_id == user_id,
                CollectionSpecVersion.task_id == task_id,
            )
            .order_by(CollectionSpecVersion.version.desc())
            .limit(1)
        )

    def records_for_task(self, *, user_id: int, task_id: int) -> list[Record]:
        return list(
            self._db.scalars(
                select(Record).where(Record.user_id == user_id, Record.task_id == task_id)
            )
        )

    # ---- 来源覆盖（M-09/URLResource 口径，同 M-12 quality.py）----
    def url_resources(self, *, user_id: int, task_id: int) -> list[URLResource]:
        return list(
            self._db.scalars(
                select(URLResource).where(
                    URLResource.user_id == user_id, URLResource.task_id == task_id
                )
            )
        )

    @staticmethod
    def record_field_value(record: Record, field_name: str) -> object:
        """字段值：真实提取记录在 payload.values；fixture/旧数据平铺在 payload。"""
        payload = record.payload or {}
        values = payload.get("values")
        if isinstance(values, dict):
            return values.get(field_name)
        return payload.get(field_name)

    @staticmethod
    def record_source(record: Record, url_by_id: dict[int, URLResource]) -> str | None:
        """记录来源：payload.source_type（fixture/旧数据）或 URLResource.source_type（真实）。"""
        payload = record.payload or {}
        payload_source = payload.get("source_type")
        if payload_source:
            return str(payload_source)
        if record.url_resource_id is not None:
            url = url_by_id.get(record.url_resource_id)
            if url is not None:
                return url.source_type
        return None
