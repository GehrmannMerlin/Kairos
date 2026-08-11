"""Typed PlanGraphDraft + canonical validation result (M-08 / D-008).

LLM 只负责提出 Plan；判定合法性的 enum 只有这一组，禁止新增语义重复的
WAITING_CONFIRMATION / NEEDS_USER / BLOCK_APPROVAL 等第二套结果名。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.task_types import TaskType
from app.plan.nodes import NodeType, ResourceKind


class ResourceRef(BaseModel):
    kind: ResourceKind
    ref_key: str  # 稳定引用，如 seed:1 / batch:unit-1


class PlanNodeInstance(BaseModel):
    node_id: str
    node_type: NodeType
    definition_version: str
    parameters: dict = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    optional: bool = False
    fail_policy: Literal["block", "skip", "retry"] = "block"


class PlanEdge(BaseModel):
    from_node_id: str
    to_node_id: str
    resource_refs: list[ResourceRef] = Field(default_factory=list)


class PlanGraphDraft(BaseModel):
    schema_version: str = "m08.1"
    task_id: int
    spec_version: int
    task_type: TaskType
    nodes: list[PlanNodeInstance] = Field(default_factory=list)
    edges: list[PlanEdge] = Field(default_factory=list)
    reasoning_summary: str | None = None  # 只保存可审计摘要，不保存 LLM chain-of-thought


class PlanValidationResult(StrEnum):
    VALID = "VALID"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
    REQUIRES_NEW_SPEC = "REQUIRES_NEW_SPEC"
    INVALID = "INVALID"
    PROHIBITED = "PROHIBITED"


class PlanValidationIssue(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    path: str | None = None
