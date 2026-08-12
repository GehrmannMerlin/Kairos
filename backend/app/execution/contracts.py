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


class DagNodeExecution(BaseModel):
    """节点级执行事实（只读，来自事件/URL/Record 可获得的证据；缺失显示 0/—）。"""

    model_config = _STRICT

    event_count: int = 0
    last_status: str | None = None
    last_error: str | None = None
    attempt_count: int = 0
    tool: str | None = None
    duration_ms: int | None = None
    url_fetched_count: int = 0
    record_count: int = 0


class DagNodeDto(BaseModel):
    model_config = _STRICT

    node_id: str
    node_type: str
    definition_version: str
    resource_class: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    optional: bool = False
    fail_policy: str = "block"
    stage: str
    parameters_summary: dict = Field(default_factory=dict)
    execution: DagNodeExecution = Field(default_factory=DagNodeExecution)


class DagEdge(BaseModel):
    model_config = _STRICT

    from_node_id: str
    to_node_id: str


class DagView(BaseModel):
    model_config = _STRICT

    task_id: int
    plan_version: int
    spec_version: int
    validation_status: str
    stage_status: dict[str, str] = Field(default_factory=dict)
    nodes: list[DagNodeDto] = Field(default_factory=list)
    edges: list[DagEdge] = Field(default_factory=list)


class NodeDetailDto(BaseModel):
    """Node Detail Drawer 数据：冻结定义 + 可获得的执行证据，无大型原始 payload。"""

    model_config = _STRICT

    node_id: str
    node_type: str
    definition_version: str
    resource_class: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    optional: bool = False
    fail_policy: str = "block"
    plan_version: int
    stage: str
    run: RunSummary | None = None
    parameters_summary: dict = Field(default_factory=dict)
    execution: DagNodeExecution = Field(default_factory=DagNodeExecution)
