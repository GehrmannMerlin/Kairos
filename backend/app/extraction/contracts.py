"""M-11 typed extraction contracts (D-008 / D-010). All extractors share one typed result."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


class ExtractorMethod(StrEnum):
    """Canonical extraction method vocabulary (no second set of names)."""

    JSON_LD = "json_ld"
    META = "meta"
    TABLE = "table"
    CSS = "css"
    XPATH = "xpath"
    RULE = "rule"
    LLM = "llm"


class CandidateValidationStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNRESOLVED = "unresolved"


class RecordPartition(StrEnum):
    """M-11 only forms EXTRACTED record candidates. PASSED/NEEDS_REVIEW/REJECTED are M-12."""

    EXTRACTED = "extracted"


class ExtractionIssue(BaseModel):
    model_config = _STRICT

    code: str
    field_name: str | None = None
    detail: str = ""
    method: ExtractorMethod | None = None


class ExtractionCandidate(BaseModel):
    """A field-level extraction candidate. NOT a PASSED record value."""

    model_config = _STRICT

    field_name: str
    raw_value: str
    normalized_value: str | None = None
    value_type: str = "text"
    method: ExtractorMethod
    confidence: float
    extractor_version: str
    rule_version: int | None = None
    model_config_id: str | None = None  # LLM only, metadata, never a secret
    source_locator: str | None = None
    raw_snippet: str | None = None
    validation_status: CandidateValidationStatus = CandidateValidationStatus.VALID
    issue_code: str | None = None
    evidence_ref: int | None = None  # field_evidence.id after persistence


class ExtractionResult(BaseModel):
    """Unified typed return of every extractor and the whole ladder."""

    model_config = _STRICT

    snapshot_id: int
    schema_version: str
    extractor_type: str
    extractor_version: str
    candidates: list[ExtractionCandidate] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    issues: list[ExtractionIssue] = Field(default_factory=list)
    duration_ms: int = 0
    technical_metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionSettings:
    """Central extraction thresholds — never hard-code them in extractors/tests."""

    extractor_version: str = "m11.1"
    schema_version: str = "m11.1"
    max_context_bytes: int = 30_000
    max_context_chars: int = 30_000
    max_snippet_chars: int = 500
    # LLM fallback 专用：单次 typed extraction 的正文上限远小于结构化上下文，避免 30K 字符
    # 中文正文让 DeepSeek 单请求超过 provider_inference_timeout（45s）→ 0 records。
    llm_max_context_chars: int = 12_000
    # 超时后「缩小上下文」重试的上限（D-013：重试必须改变输入，而非同样 prompt 无限再试）。
    llm_retry_reduced_context_chars: int = 6_000
    # M-11 小批次：单次 Extract Activity 最多处理的快照数（D-015 小批次事务）。
    # 预算与 Activity timeout 层级：budget(100s) + 最坏单快照(90s) < Activity timeout(200s)。
    extract_batch_size: int = 5
    extract_activity_budget_seconds: int = 100
    min_rule_validation_samples: int = 3
    min_rule_precision: float = 0.9
    min_rule_coverage: float = 0.5
    llm_max_repairs: int = 1
    max_candidates_per_field: int = 8
    allow_llm_fallback: bool = True
    rule_failure_before_stale: int = 3
