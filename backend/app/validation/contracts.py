"""M-12 canonical validation/quality/completion typed contracts (D-006 / D-014).

结果分区只有 PASSED / NEEDS_REVIEW / REJECTED。RecordPartition.EXTRACTED（M-11）
是内部候选状态，不面向用户。禁止新增 VALID/FAILED/PENDING_VALIDATION 第二套分区名。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

_STRICT = ConfigDict(extra="forbid")


class ValidationPartition(StrEnum):
    PASSED = "passed"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class ReviewReason(StrEnum):
    MISSING_REQUIRED = "missing_required"
    UNRESOLVED_CONFLICT = "unresolved_conflict"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    LOW_EVIDENCE_CONFIDENCE = "low_evidence_confidence"
    RULE_MISMATCH = "rule_mismatch"
    INVALID_FORMAT = "invalid_format"
    BUSINESS_RULE_FAILED = "business_rule_failed"


class AllowedReviewAction(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"
    AGENT_REEVALUATE = "agent_reevaluate"
    MERGE_DUPLICATE = "merge_duplicate"
    RESOLVE_CONFLICT = "resolve_conflict"


class ValidationIssue(BaseModel):
    model_config = _STRICT

    code: str
    field_name: str | None = None
    detail: str = ""
    severity: str = "error"  # error | warning


class ValidationResult(BaseModel):
    """canonical 单条 Record 验证结果。字段按现有 domain 对齐，不用 dict[str, Any] 做核心事实。"""

    model_config = _STRICT

    record_id: int
    spec_version_id: int
    validation_version: str
    structural_issues: list[ValidationIssue] = []
    required_field_issues: list[ValidationIssue] = []
    evidence_issues: list[ValidationIssue] = []
    business_rule_issues: list[ValidationIssue] = []
    dedupe_group_id: int | None = None
    dedupe_result: dict = {}
    conflict_result: dict = {}
    partition: ValidationPartition
    review_type: str | None = None
    review_reason: ReviewReason | None = None
    allowed_actions: list[str] = []
    quality_contribution: dict = {}
    validated_at: datetime
