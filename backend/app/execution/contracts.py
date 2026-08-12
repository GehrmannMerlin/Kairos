"""M-14 Execution Query typed contracts（D-055/D-063）。

ExecutionView 是用户可理解的执行解释页：阶段聚合来自 Run/DomainEvent/URLResource/
Record 真实事实，前端只渲染。TimelineEvent 只暴露 allowlist 字段，绝不返回原始 payload。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


class StageKey(StrEnum):
    GOAL_PLAN = "goal_plan"
    SOURCE_DISCOVERY = "source_discovery"
    FETCH = "fetch"
    EXTRACTION = "extraction"
    VALIDATION = "validation"


StageState = Literal["not_started", "in_progress", "completed", "partial", "failed"]
TimelineCategory = Literal[
    "error", "retry", "tool_upgrade", "plan_change", "model_call", "pause_resume"
]


class RunSummary(BaseModel):
    model_config = _STRICT

    run_id: int
    state: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    plan_version: int
    spec_version: int


class StageSummary(BaseModel):
    model_config = _STRICT

    key: StageKey
    label: str
    state: StageState
    event_count: int
    url_processed: int = 0
    record_count: int = 0
    error_count: int = 0


class PlanBrief(BaseModel):
    model_config = _STRICT

    plan_version: int
    node_count: int
    validation_status: str


class ExecutionView(BaseModel):
    model_config = _STRICT

    task_id: int
    run: RunSummary | None = None
    stages: list[StageSummary] = Field(default_factory=list)
    urls: dict[str, int] = Field(default_factory=dict)
    records: dict[str, int] = Field(default_factory=dict)
    plan: PlanBrief | None = None


class TimelineEvent(BaseModel):
    """安全脱敏事件摘要。字段全部来自 allowlist，禁止透传 DomainEvent.payload。"""

    model_config = _STRICT

    event_id: int
    timestamp: datetime
    categories: list[TimelineCategory]
    stage: str
    summary: str
    status: str | None = None
    error_code: str | None = None
    run_id: int | None = None
    node_run_id: int | None = None
    node_id: str | None = None
    retry_count: int = 0
    tool: str | None = None
    model: str | None = None
    duration_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    evidence_refs: list[int] = Field(default_factory=list)
    trace_ref: str | None = None


class TimelinePage(BaseModel):
    model_config = _STRICT

    task_id: int
    items: list[TimelineEvent] = Field(default_factory=list)
    next_cursor: int | None = None
    has_more: bool = False
