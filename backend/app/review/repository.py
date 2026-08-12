"""M-13 ReviewRepository：records 查询 + field overrides + review audit（D-041/042/060/061）。

所有查询强制 user_id 边界；跨用户访问返回 NotFoundError(404)，不泄漏存在性。
create_override/create_review_action 只 flush（不 commit），service 统一单事务提交（D-015）。

查询策略：
- partition / review_type 走 SQL 列过滤（Postgres + SQLite 一致）。
- payload 内筛选（field/value AND、source_type、extract_method、min_confidence、q 搜索）
  统一走 Python 侧扫描任务内 records，产出 id 集合后再 SQL 分页 —— 跨方言安全，
  不依赖 JSON 算子；任务内记录数有界，符合 M-13 大数据集分页不落前端的验收。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.auth.errors import NotFoundError
from app.domain.models import (
    FieldEvidence,
    Record,
    RecordFieldOverride,
    RecordReviewAction,
    URLResource,
)
from app.review.contracts import RecordListParams

SORTABLE_COLUMNS: dict[str, Any] = {
    "id": Record.id,
    "created_at": Record.created_at,
    "updated_at": Record.updated_at,
}


def _owned(db: Any, model: type, user_id: int, obj_id: int) -> Any:
    row = db.get(model, obj_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError("资源不存在")
    return row


def _payload_matches(payload: dict, needle: str) -> bool:
    for value in payload.values():
        if isinstance(value, str) and needle in value.lower():
            return True
        if isinstance(value, (int, float, bool)) and needle in str(value).lower():
            return True
    return False


def _payload_ok(
    payload: dict,
    *,
    field: str | None,
    value: str | None,
    source_type: str | None,
    effective_source: str | None,
    extract_method: str | None,
    min_confidence: float | None,
    needle: str | None,
) -> bool:
    if field and value is not None and str(payload.get(field)) != value:
        return False
    # source_type 由调用方解析为 effective_source（URLResource 优先，payload 兜底）
    if source_type is not None and effective_source != source_type:
        return False
    if extract_method and payload.get("extract_method") != extract_method:
        return False
    if min_confidence is not None:
        raw = payload.get("confidence")
        try:
            conf = None if raw is None else float(str(raw))
        except (TypeError, ValueError):
            conf = None
        if conf is None or conf < min_confidence:
            return False
    if needle:
        return _payload_matches(payload, needle)
    return True


def _effective_source(record: Record, url_source_map: dict[int, str]) -> str | None:
    """记录来源：payload.source_type（fixture/旧数据）或 URLResource.source_type（真实）。

    真实采集记录（M-11）payload 不含 source_type，因此必须解析 url_resource_id →
    URLResource.source_type，否则 D-062 的来源下钻对真实数据永远返回空。
    """
    payload = record.payload or {}
    payload_source = payload.get("source_type")
    if payload_source:
        return str(payload_source)
    if record.url_resource_id is not None:
        return url_source_map.get(record.url_resource_id)
    return None


class ReviewRepository:
    def __init__(self, db: Any) -> None:
        self._db = db

    def get_record_owned(self, *, user_id: int, record_id: int) -> Record:
        return _owned(self._db, Record, user_id, record_id)

    def count_by_partition(self, *, user_id: int, task_id: int) -> dict[str, int]:
        rows = self._db.execute(
            select(Record.partition, func.count())
            .where(Record.user_id == user_id, Record.task_id == task_id)
            .group_by(Record.partition)
        ).all()
        return {p: int(c) for p, c in rows}

    def query_records(
        self, *, user_id: int, task_id: int, params: RecordListParams
    ) -> tuple[int, list[Record]]:
        stmt = select(Record).where(Record.user_id == user_id, Record.task_id == task_id)
        if params.partition:
            stmt = stmt.where(Record.partition == params.partition)
        if params.review_type:
            stmt = stmt.where(Record.review_type == params.review_type)

        has_payload_filter = bool(
            (params.field and params.value)
            or params.source_type
            or params.extract_method
            or params.min_confidence is not None
            or params.q
        )
        if has_payload_filter:
            records = list(
                self._db.scalars(
                    select(Record).where(Record.user_id == user_id, Record.task_id == task_id)
                )
            )
            # source_type 真实解析：URLResource.source_type（owner-safe 范围）
            url_source_map: dict[int, str] = {}
            if params.source_type:
                url_source_map = {
                    r.id: r.source_type
                    for r in self._db.scalars(
                        select(URLResource).where(
                            URLResource.user_id == user_id, URLResource.task_id == task_id
                        )
                    )
                }
            ids = [
                r.id
                for r in records
                if _payload_ok(
                    r.payload,
                    field=params.field,
                    value=params.value,
                    source_type=params.source_type,
                    effective_source=_effective_source(r, url_source_map),
                    extract_method=params.extract_method,
                    min_confidence=params.min_confidence,
                    needle=params.q.lower() if params.q else None,
                )
            ]
            stmt = stmt.where(Record.id.in_(ids))

        total = int(self._db.scalar(select(func.count()).select_from(stmt.subquery())))
        sort_col = SORTABLE_COLUMNS.get(params.sort_by or "", Record.created_at)
        order = sort_col.asc() if params.sort_order == "asc" else sort_col.desc()
        result = list(
            self._db.scalars(
                stmt.order_by(order)
                .offset((params.page - 1) * params.page_size)
                .limit(params.page_size)
            )
        )
        return total, result

    def query_records_all(
        self, *, user_id: int, task_id: int, params: RecordListParams
    ) -> list[Record]:
        """与 query_records 相同 filter 语义，不分页，固定 record.id ASC（M-15 导出确定性）。"""
        params = params.model_copy(
            update={"page": 1, "page_size": 10**9, "sort_by": "id", "sort_order": "asc"}
        )
        _, rows = self.query_records(user_id=user_id, task_id=task_id, params=params)
        return rows

    def list_overrides(self, *, user_id: int, record_id: int) -> list[RecordFieldOverride]:
        return list(
            self._db.scalars(
                select(RecordFieldOverride)
                .where(
                    RecordFieldOverride.user_id == user_id,
                    RecordFieldOverride.record_id == record_id,
                )
                .order_by(RecordFieldOverride.id)
            )
        )

    def evidence_for_record(self, *, user_id: int, record_id: int) -> list[FieldEvidence]:
        return list(
            self._db.scalars(
                select(FieldEvidence)
                .where(FieldEvidence.user_id == user_id, FieldEvidence.record_id == record_id)
                .order_by(FieldEvidence.id)
            )
        )

    def url_for_record(self, *, record: Record) -> str | None:
        if record.url_resource_id is None:
            return None
        row = self._db.get(URLResource, record.url_resource_id)
        return row.url if row else None

    def create_override(
        self,
        *,
        user_id: int,
        task_id: int,
        record_id: int,
        field_name: str,
        original_value: str | None,
        final_value: str | None,
        modified_by: int,
    ) -> RecordFieldOverride:
        row = RecordFieldOverride(
            user_id=user_id,
            task_id=task_id,
            record_id=record_id,
            field_name=field_name,
            original_value=original_value,
            final_value=final_value,
            value_source="USER_OVERRIDE",
            modified_by=modified_by,
        )
        self._db.add(row)
        return row

    def create_review_action(
        self,
        *,
        user_id: int,
        task_id: int,
        record_id: int,
        action_type: str,
        review_type: str | None,
        review_reason: str | None,
        batch_operation_id: str | None,
        reason: str | None,
        reviewed_by: int,
        detail: dict | None = None,
    ) -> RecordReviewAction:
        row = RecordReviewAction(
            user_id=user_id,
            task_id=task_id,
            record_id=record_id,
            action_type=action_type,
            review_type=review_type,
            review_reason=review_reason,
            batch_operation_id=batch_operation_id,
            reason=reason,
            reviewed_by=reviewed_by,
            detail=detail,
        )
        self._db.add(row)
        return row
