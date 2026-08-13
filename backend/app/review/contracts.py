"""M-13 data/review typed contracts（D-041/D-060/D-061/D-062）。

RecordView 是查询/审核统一返回契约；partition 只来自 M-12 三分区。allowed_actions
由 ReviewPolicy 派生（后端事实驱动），前端不得复制。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

_STRICT = ConfigDict(extra="forbid")


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    AGENT_REEVALUATE = "agent_reevaluate"


class FieldEdit(BaseModel):
    model_config = _STRICT

    field_name: str
    final_value: str | None


class RecordView(BaseModel):
    model_config = _STRICT

    record_id: int
    task_id: int
    partition: str
    review_type: str | None
    review_reason: str | None
    data_version: int
    fields: dict
    source_url: str | None = None
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]


class RecordFieldDetail(BaseModel):
    model_config = _STRICT

    field_name: str
    value: str | None
    original_value: str | None = None
    value_source: str = "EXTRACTED"
    extract_method: str | None = None
    extractor_version: str | None = None
    confidence: float | None = None
    source_url: str | None = None
    snapshot_id: int | None = None


class RecordDetailView(BaseModel):
    model_config = _STRICT

    record_id: int
    task_id: int
    partition: str
    review_type: str | None
    review_reason: str | None
    data_version: int
    allowed_actions: list[str]
    fields: list[RecordFieldDetail]
    created_at: datetime
    updated_at: datetime


class RecordListParams(BaseModel):
    model_config = _STRICT

    partition: Literal["passed", "needs_review", "rejected"] | None = None
    q: str | None = None  # 跨字符串字段全文搜索（后端执行）
    field: str | None = None  # 字段筛选名
    value: str | None = None  # 字段筛选值（精确匹配）
    source_type: str | None = None
    extract_method: str | None = None
    min_confidence: float | None = None
    review_type: str | None = None
    sort_by: str | None = None  # 可排序字段白名单见 repository
    sort_order: Literal["asc", "desc"] = "asc"
    page: int = 1
    page_size: int = 20


class RecordListResponse(BaseModel):
    model_config = _STRICT

    task_id: int
    partition_counts: dict[str, int]
    items: list[RecordView]
    total: int
    page: int
    page_size: int
    dataset_version: str | None = None


class RecordReviewCommand(BaseModel):
    model_config = _STRICT

    action: ReviewAction
    reason: str | None = None
    edits: list[FieldEdit] = []
    expected_data_version: int


class RecordReviewResponse(BaseModel):
    model_config = _STRICT

    record: RecordView


class BatchReviewCommand(BaseModel):
    model_config = _STRICT

    action: Literal["approve", "reject", "agent_reevaluate"]
    record_ids: list[int]
    reason: str | None = None
    expected_data_versions: dict[int, int] = {}


class BatchReviewItem(BaseModel):
    model_config = _STRICT

    record_id: int
    ok: bool
    partition: str | None = None
    error: str | None = None


class BatchReviewResponse(BaseModel):
    model_config = _STRICT

    batch_operation_id: str
    results: list[BatchReviewItem]
