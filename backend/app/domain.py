from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkspacePermission(StrEnum):
    NONE = "NONE"
    READ_ONLY = "READ_ONLY"
    READ_WRITE = "READ_WRITE"
    LOCAL_FULL_ACCESS = "LOCAL_FULL_ACCESS"


class TaskStatus(StrEnum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"


class TaskRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"


class CollectionMode(StrEnum):
    SPECIFIED_SOURCE = "SPECIFIED_SOURCE"
    EXPLORATORY = "EXPLORATORY"
    HYBRID = "HYBRID"


class CollectionFieldType(StrEnum):
    STRING = "STRING"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    URL = "URL"


class CollectionSourceStatus(StrEnum):
    PENDING = "PENDING"
    FETCHED = "FETCHED"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class CollectionSourceOrigin(StrEnum):
    SEED = "SEED"
    SEARCH = "SEARCH"


class SearchRoundStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RecordStatus(StrEnum):
    PASSED = "PASSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"


class CollectionError(RuntimeError):
    """A bounded business error that can cross an Activity/tool boundary safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message[:500]
        super().__init__(f"{code}: {self.message}")


_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.DRAFT: {TaskStatus.DRAFT, TaskStatus.RUNNING},
    TaskStatus.RUNNING: {
        TaskStatus.RUNNING,
        TaskStatus.COMPLETED,
        TaskStatus.PARTIALLY_COMPLETED,
        TaskStatus.FAILED,
    },
    TaskStatus.COMPLETED: {TaskStatus.COMPLETED},
    TaskStatus.PARTIALLY_COMPLETED: {TaskStatus.PARTIALLY_COMPLETED},
    TaskStatus.FAILED: {TaskStatus.FAILED},
}

_TASK_RUN_TRANSITIONS: dict[TaskRunStatus, set[TaskRunStatus]] = {
    TaskRunStatus.RUNNING: {
        TaskRunStatus.RUNNING,
        TaskRunStatus.COMPLETED,
        TaskRunStatus.PARTIALLY_COMPLETED,
        TaskRunStatus.FAILED,
    },
    TaskRunStatus.COMPLETED: {TaskRunStatus.COMPLETED},
    TaskRunStatus.PARTIALLY_COMPLETED: {TaskRunStatus.PARTIALLY_COMPLETED},
    TaskRunStatus.FAILED: {TaskRunStatus.FAILED},
}


def transition_task_status(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    if target not in _TASK_TRANSITIONS[current]:
        raise CollectionError("INVALID_STATE_TRANSITION", f"cannot change task from {current} to {target}")
    return target


def transition_task_run_status(current: TaskRunStatus, target: TaskRunStatus) -> TaskRunStatus:
    if target not in _TASK_RUN_TRANSITIONS[current]:
        raise CollectionError(
            "INVALID_STATE_TRANSITION", f"cannot change task run from {current} to {target}"
        )
    return target


_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class CollectionFieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    type: CollectionFieldType
    required: bool
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _FIELD_NAME.fullmatch(value):
            raise ValueError("field name must match ^[a-z][a-z0-9_]{0,63}$")
        return value


class SearchLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_search_rounds: int = Field(default=5, ge=1, le=10)
    max_results_per_round: int = Field(default=10, ge=1, le=20)
    max_discovered_sources: int = Field(default=50, ge=1, le=100)
    max_processed_sources: int = Field(default=30, ge=1, le=100)


def _normalize_scope_domain(value: str) -> str:
    normalized = value.strip().lower().removeprefix("*.")
    if not normalized or "/" in normalized or ":" in normalized or " " in normalized:
        raise ValueError("scope domains must be hostnames")
    return normalized.rstrip(".")


class CollectionSpecConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=20_000)
    fields: list[CollectionFieldSpec] = Field(min_length=1, max_length=100)
    seed_urls: list[str] = Field(default_factory=list, max_length=100)
    target_count: int | None = Field(default=None, ge=1)
    mode: CollectionMode = CollectionMode.SPECIFIED_SOURCE
    scope_domains: list[str] = Field(default_factory=list, max_length=50)
    search_limits: SearchLimits = Field(default_factory=SearchLimits)

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("goal must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_collection_mode(self) -> CollectionSpecConfirm:
        if len({field.name for field in self.fields}) != len(self.fields):
            raise ValueError("field names must be unique")
        if any(not url.strip() for url in self.seed_urls):
            raise ValueError("seed URLs must not be empty")
        if self.mode is CollectionMode.SPECIFIED_SOURCE and not self.seed_urls:
            raise ValueError("at least one seed URL is required")
        if self.mode in {CollectionMode.EXPLORATORY, CollectionMode.HYBRID} and self.target_count is None:
            raise ValueError("target_count is required for exploratory collection")
        if self.mode is CollectionMode.HYBRID and not self.seed_urls and not self.scope_domains:
            raise ValueError("at least one seed URL or scope domain is required for hybrid collection")
        self.scope_domains = [_normalize_scope_domain(value) for value in self.scope_domains]
        if len(set(self.scope_domains)) != len(self.scope_domains):
            raise ValueError("scope domains must be unique")
        return self


class CollectionSpecVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_version_id: str
    owner_id: str
    task_id: str
    version: int
    mode: CollectionMode
    goal: str
    fields: list[CollectionFieldSpec]
    seed_urls: list[str]
    target_count: int | None = None
    scope_domains: list[str] = Field(default_factory=list)
    search_limits: SearchLimits = Field(default_factory=SearchLimits)
    confirmed_at: datetime
    created_at: datetime


class CollectionSourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    url: str
    canonical_url: str
    origin: CollectionSourceOrigin = CollectionSourceOrigin.SEED
    status: CollectionSourceStatus
    snapshot_id: str | None = None
    failure_code: str | None = None
    title: str | None = None
    snippet: str = Field(default="", max_length=500)


class CollectionExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_version_id: str
    mode: CollectionMode
    goal: str
    fields: list[CollectionFieldSpec]
    sources: list[CollectionSourceSummary]
    target_count: int | None = None
    scope_domains: list[str] = Field(default_factory=list)
    search_limits: SearchLimits = Field(default_factory=SearchLimits)


class SearchSourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    url: str
    title: str
    snippet: str = Field(max_length=500)
    rank: int = Field(ge=1)


class SearchSourcesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_round_id: str
    query: str
    returned_results: int
    new_sources_count: int
    sources: list[SearchSourceResult] = Field(max_length=20)


class SearchRoundSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_round_id: str
    task_run_id: str
    round_number: int
    query: str
    query_hash: str
    provider: str
    requested_results: int
    returned_results: int
    accepted_results: int
    new_sources: int
    passed_records_before: int
    passed_records_after: int
    new_passed_records: int
    status: SearchRoundStatus
    created_at: datetime
    completed_at: datetime | None = None


class EvidenceSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: str = Field(max_length=1000)
    confidence: float | None = Field(default=None, ge=0, le=1)


class RecordSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, EvidenceSubmission] = Field(default_factory=dict)


class CommitExtractionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    records: list[RecordSubmission] = Field(default_factory=list, max_length=100)
    source_complete: bool = True


class FetchSourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str | None = None
    source_id: str
    url: str
    final_url: str | None = None
    status: CollectionSourceStatus
    status_code: int | None = None
    content_type: str | None = None
    title: str | None = None
    content_hash: str | None = None
    bytes_read: int = 0
    text_chars: int = 0
    text_preview: str = Field(default="", max_length=2000)
    failure_code: str | None = None
    failure_message: str | None = Field(default=None, max_length=500)


class InspectSnapshotResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    url: str
    title: str | None = None
    offset: int
    content: str = Field(max_length=6000)
    next_offset: int
    has_more: bool


class CollectionProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_sources: int
    pending_sources: int
    fetched_sources: int
    processed_sources: int
    failed_sources: int
    blocked_sources: int
    total_records: int
    passed_records: int
    needs_review_records: int
    rejected_records: int
    remaining_sources: int
    skipped_sources: int = 0
    mode: CollectionMode = CollectionMode.SPECIFIED_SOURCE
    target_count: int | None = None
    passed_canonical_records: int = 0
    observations_total: int = 0
    canonical_records_total: int = 0
    remaining_to_target: int | None = None
    search_rounds_completed: int = 0
    max_search_rounds: int = 0
    sources_discovered: int = 0
    new_sources_last_round: int = 0
    last_round_new_passed_records: int = 0
    saturation_state: str = "NOT_REACHED"
    actionable_sources: list[CollectionSourceSummary] = Field(default_factory=list, max_length=10)


class CollectionCompletionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    reason: str
    passed_records: int
    target_count: int | None
    search_rounds: int
    saturation_state: str
    remaining_runtime_budget: int | None = None


def evaluate_saturation(rounds: Sequence[SearchRoundSummary]) -> str:
    completed = [round_row for round_row in rounds if round_row.status is SearchRoundStatus.COMPLETED]
    if len(completed) >= 2 and all(row.new_passed_records == 0 for row in completed[-2:]):
        return "SATURATED"
    return "NOT_REACHED"


def evaluate_collection_completion(
    progress: CollectionProgress,
    *,
    max_discovered_sources: int,
    max_processed_sources: int,
    agent_continuations: int,
    max_agent_continuations: int,
    technical_failure: bool = False,
) -> CollectionCompletionDecision:
    """Evaluate collection completion using persisted facts, never Agent prose."""
    if technical_failure:
        decision, reason = "FAILED", "TECHNICAL_FAILURE"
    elif progress.target_count is not None and progress.passed_canonical_records >= progress.target_count:
        decision, reason = "COMPLETED", "TARGET_REACHED"
    elif agent_continuations >= max_agent_continuations:
        decision, reason = "PARTIALLY_COMPLETED", "AGENT_CONTINUATION_LIMIT"
    elif progress.saturation_state == "SATURATED":
        decision, reason = "PARTIALLY_COMPLETED", "SEARCH_SATURATED"
    elif progress.max_search_rounds and progress.search_rounds_completed >= progress.max_search_rounds:
        decision, reason = "PARTIALLY_COMPLETED", "SEARCH_ROUND_LIMIT"
    elif progress.sources_discovered >= max_discovered_sources:
        decision, reason = "PARTIALLY_COMPLETED", "DISCOVERED_SOURCE_LIMIT"
    elif progress.processed_sources >= max_processed_sources:
        decision, reason = "PARTIALLY_COMPLETED", "PROCESSED_SOURCE_LIMIT"
    else:
        decision, reason = "CONTINUE", "SEARCH_BUDGET_REMAINS"
    return CollectionCompletionDecision(
        decision=decision,
        reason=reason,
        passed_records=progress.passed_canonical_records,
        target_count=progress.target_count,
        search_rounds=progress.search_rounds_completed,
        saturation_state=progress.saturation_state,
        remaining_runtime_budget=max(max_agent_continuations - agent_continuations, 0),
    )


class CommitExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_commit_id: str
    snapshot_id: str
    record_ids: list[str]
    record_count: int
    idempotent: bool = False
    new_canonical_records: int = 0
    duplicates: int = 0
    conflicts: int = 0


class CompletionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_run_id: str
    task_status: TaskStatus
    task_run_status: TaskRunStatus
    remaining_sources: int
    summary: str = Field(max_length=500)
    decision: str | None = None
    reason: str | None = None
    passed_records: int = 0
    target_count: int | None = None


class WorkspaceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    owner_id: str
    display_name: str
    root_path: str
    permission_mode: WorkspacePermission
    enabled: bool = True


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    task_id: str
    task_run_id: str
    owner_id: str
    agent_name: str = "kairos-agent-v1"
    tool_name: str | None = None
    summary: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def bounded_payload(payload: dict[str, Any], *, max_chars: int = 4000) -> dict[str, Any]:
    """Keep event metadata bounded and exclude obvious secret-shaped fields."""
    blocked = {"api_key", "credential", "credential_plaintext", "password", "secret", "token"}
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in blocked or any(
            marker in key.lower() for marker in ("api_key", "password", "secret_key")
        ):
            continue
        if isinstance(value, str):
            clean[key] = value[:max_chars]
        elif isinstance(value, (int, float, bool)) or value is None:
            clean[key] = value
        else:
            text = str(value)
            clean[key] = text[:max_chars]
    return clean
