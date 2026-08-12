"""M-14 Quality Query typed contracts（D-062）。

QualityView 是只读诊断模型：所有指标来自数据库事实 + 最新 QualitySnapshot；
不包含任何人工修正/审核动作。drilldown 是 typed filter snapshot，前端据此生成
M-13 Data 页 Deep Link（不允许组件手工拼 URL）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


class QualityDrilldown(BaseModel):
    """M-13 Data 页可解析的 typed 筛选快照（D-062）。"""

    model_config = _STRICT

    status: Literal["passed", "review", "rejected"] | None = None
    review_type: str | None = None
    source_type: str | None = None
    extract_method: str | None = None
    min_confidence: float | None = None


class QualityMetricItem(BaseModel):
    model_config = _STRICT

    key: str
    label: str
    value: int | float
    kind: Literal["count", "rate"]
    drilldown: QualityDrilldown


class QualitySummary(BaseModel):
    model_config = _STRICT

    total_records: int
    passed: int
    needs_review: int
    rejected: int


class QualityMetricsDto(BaseModel):
    """M-12 口径指标；snapshot 存在时来自冻结 QualitySnapshot，否则按当前 DB facts 计算。"""

    model_config = _STRICT

    pass_rate: float
    missing_rate: float
    duplicate_rate: float
    conflict_count: int
    source_coverage: float
    sampling_accuracy: float | None = None


class QualityDiagnostics(BaseModel):
    model_config = _STRICT

    missing_required: int
    unresolved_conflict: int
    possible_duplicate: int
    low_confidence: int
    rejected: int


class FieldCompletenessRow(BaseModel):
    model_config = _STRICT

    field_name: str
    total: int
    non_null: int
    missing: int
    completion_rate: float


class SourceCoverageRow(BaseModel):
    model_config = _STRICT

    source_type: str
    eligible: bool
    covered: bool
    record_count: int


class SamplingSummary(BaseModel):
    model_config = _STRICT

    sample_count: int
    accuracy: float | None = None
    sample_refs: list[dict] = Field(default_factory=list)


class QualityView(BaseModel):
    """Metrics Version Boundary：页面刷新不会静默换 Dataset。"""

    model_config = _STRICT

    task_id: int
    dataset_version: str | None = None
    validation_version: str | None = None
    sampling_policy_version: str | None = None
    spec_version: int | None = None
    run_id: int | None = None
    snapshot_id: int | None = None
    snapshot_created_at: datetime | None = None
    summary: QualitySummary
    metrics: QualityMetricsDto
    field_completeness: list[FieldCompletenessRow] = Field(default_factory=list)
    source_coverage: list[SourceCoverageRow] = Field(default_factory=list)
    diagnostics: QualityDiagnostics
    sampling: SamplingSummary
    items: list[QualityMetricItem] = Field(default_factory=list)
