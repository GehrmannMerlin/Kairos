"""M-15 Artifact / Export / Completion typed contracts（D-060/D-065/D-072）。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


class ExportType(StrEnum):
    FORMAL = "formal"  # 正式：PASSED only
    REVIEW = "review"  # 待复核：NEEDS_REVIEW + review 字段
    AUDIT = "audit"  # 审核完整：三分区 + 状态/审核字段


class ExportScope(StrEnum):
    CURRENT = "current"  # 当前 Data 页筛选结果
    ALL = "all"  # 全部当前分区


class ExportFilter(BaseModel):
    """与 M-13 RecordListParams 对齐的筛选子集（不含 partition/page/sort）。"""

    model_config = _STRICT
    q: str | None = None
    field: str | None = None
    value: str | None = None
    source_type: str | None = None
    extract_method: str | None = None
    min_confidence: float | None = None
    review_type: str | None = None


class ExportRequest(BaseModel):
    model_config = _STRICT
    export_type: ExportType
    scope: ExportScope = ExportScope.ALL
    filter: ExportFilter = Field(default_factory=ExportFilter)


class ArtifactRef(BaseModel):
    model_config = _STRICT
    artifact_id: int
    content_hash: str
    download_url: str
    row_count: int


class ArtifactView(BaseModel):
    model_config = _STRICT
    artifact_id: int
    export_type: str
    dataset_version: str
    filter_snapshot: dict
    schema_version: str | None
    row_count: int
    size_bytes: int | None
    content_hash: str
    filename: str
    status: str
    created_at: datetime
    download_url: str


class CompletionCardView(BaseModel):
    model_config = _STRICT
    task_id: int
    completion_id: int | None  # CompletionDecision 稳定 identity（幂等渲染）
    status: str  # NORMAL_COMPLETED | PARTIALLY_COMPLETED
    reason: str | None
    completion_type: str | None
    is_partial: bool
    qualified_record_count: int
    partition_counts: dict[str, int] = {}
    url_processed: int = 0  # URLResource 终态数
    runtime_limit_reason: str | None = None
    scope_completion_metadata: dict = {}
    can_view_data: bool = True
    can_view_quality: bool = True
    can_export_formal: bool = False
    can_export_review: bool = False


class PermanentDeleteCommand(BaseModel):
    model_config = _STRICT
    confirmed: bool = False
