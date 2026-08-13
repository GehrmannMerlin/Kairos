"""Typed CollectionTemplate spec (D-047/D-054).

A template stores a CollectionSpec skeleton + variables — never Run/Record/
Evidence/Checkpoint execution state.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.spec import (
    CompletionCondition,
    FieldSpec,
    RuntimeLimits,
)
from app.domain.task_types import TaskType


class TemplateVariableSpec(BaseModel):
    name: str
    label: str = ""
    type: str = "text"
    required: bool = False
    default: str | None = None


class TemplateSpec(BaseModel):
    name: str
    task_type: TaskType
    goal_template: str
    variables: list[TemplateVariableSpec] = Field(default_factory=list)
    field_schema: list[FieldSpec] = Field(default_factory=list)
    completion_conditions: list[CompletionCondition] = Field(default_factory=list)
    advanced_settings: RuntimeLimits = Field(default_factory=RuntimeLimits)
    field_expansion: dict = Field(default_factory=dict)
    default_model_config_ref: dict | None = None
