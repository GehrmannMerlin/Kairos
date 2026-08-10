"""Typed Goal Understanding output (M-06).

The GoalUnderstandingAgent returns exactly this structure; pydantic-ai validates
the model's JSON against it, so the spec draft and clarification decisions are
typed rather than free-form markdown.
"""

from __future__ import annotations

from app.domain.spec import (
    CompletionCondition,
    FieldSpec,
    RuntimeLimits,
    SourceScope,
    TemplateVariableSuggestion,
)
from app.domain.task_types import TaskType
from pydantic import BaseModel, Field


class GoalUnderstandingResult(BaseModel):
    task_type: TaskType
    goal: str
    fields: list[FieldSpec] = Field(default_factory=list)
    auto_expand_fields: bool = False
    source_scope: SourceScope = Field(default_factory=SourceScope)
    completion_conditions: list[CompletionCondition] = Field(default_factory=list)
    advanced_runtime_limits: RuntimeLimits = Field(default_factory=RuntimeLimits)
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguities: list[str] = Field(default_factory=list)
    clarification_required: bool = False
    clarification_question: str | None = None
    template_variables: list[TemplateVariableSuggestion] | None = None
