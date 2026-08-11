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
    min_rule_validation_samples: int = 3
    min_rule_precision: float = 0.9
    min_rule_coverage: float = 0.5
    llm_max_repairs: int = 1
    max_candidates_per_field: int = 8
    allow_llm_fallback: bool = True
    rule_failure_before_stale: int = 3
