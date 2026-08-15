"""Typed CollectionSpec schema shared by Draft / confirm / Templates (M-06).

One canonical vocabulary: task_type is always a ``TaskType`` value. The draft
payload is validated with these models on the server before it is persisted or
confirmed; templates store the same skeleton (goal_template/variables/field
schema/completion conditions/advanced settings) without any execution state.

D-036: no money / budget / RMB fields anywhere in the spec. D-071 concurrency is
deployment configuration and must not appear as a per-user spec parameter.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.task_types import TaskType


class FieldType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    URL = "url"
    EMAIL = "email"
    PHONE = "phone"
    DATE = "date"
    BOOLEAN = "boolean"
    OTHER = "other"


class FieldSpec(BaseModel):
    name: str
    type: FieldType = FieldType.TEXT
    required: bool = False
    description: str | None = None


class SourceScope(BaseModel):
    """Where the collection sources come from (D-003/D-034).

    mode reuses the canonical TaskType vocabulary so no second set of
    EXPLORATORY/SPECIFIED/SEARCH names can leak in.
    """

    mode: TaskType = TaskType.EXPLORATORY
    seed_urls: list[str] = Field(default_factory=list)
    source_hints: list[str] = Field(default_factory=list)
    resolution_scope: Literal["NAMED_SOURCE_ONLY"] | None = None


class CompletionCondition(BaseModel):
    """D-006 multi-condition completion. M-06 only defines the schema; the real
    saturation / coverage detection is implemented by later execution modules.
    """

    kind: Literal["min_records", "range_covered", "saturation", "limit"]
    target: int | None = None
    threshold: int | None = None
    note: str | None = None


class RuntimeLimits(BaseModel):
    """Non-monetary task safety limits (D-037). Concurrency stays out of here."""

    max_pages: int | None = None
    max_duration_minutes: int | None = None
    max_retries_per_url: int | None = None


class TemplateVariableSuggestion(BaseModel):
    """D-047: a single-use value (e.g. 深圳) the agent suggests turning into a
    template variable ``{city}``. Stored on the spec draft so create_from_task()
    can variable-ize the goal without an extra model call.
    """

    name: str
    label: str
    value: str


class SpecDraftPayload(BaseModel):
    """Editable current-candidate spec for one task (D-004).

    Saving this payload does NOT freeze anything; only confirm_spec() creates an
    immutable CollectionSpecVersion from it.
    """

    schema_version: str = "m06.1"
    task_type: TaskType | None = None
    task_name: str | None = None
    goal: str
    fields: list[FieldSpec] = Field(default_factory=list)
    auto_expand_fields: bool = False
    source_scope: SourceScope = Field(default_factory=SourceScope)
    completion_conditions: list[CompletionCondition] = Field(default_factory=list)
    advanced_settings: RuntimeLimits = Field(default_factory=RuntimeLimits)
    field_expansion: dict = Field(default_factory=dict)
    template_variables: list[TemplateVariableSuggestion] = Field(default_factory=list)


def validate_spec_payload(payload: dict) -> SpecDraftPayload:
    """Server-side typed validation of a spec payload (used at save and confirm)."""
    return SpecDraftPayload.model_validate(payload)


def validate_confirmable_spec_payload(payload: dict) -> SpecDraftPayload:
    """Validate a frozen spec while keeping editable drafts permissive."""
    spec = validate_spec_payload(payload)
    if spec.source_scope.mode is TaskType.SPECIFIED_SOURCE and not spec.source_scope.seed_urls:
        from app.domain.errors import SpecValidationError

        raise SpecValidationError("指定来源任务必须提供至少一个完整网址")
    return spec
