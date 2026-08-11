"""M-12 三分区判定 + review_type/review_reason/allowed_actions（D-014 / D-061 / 模块需求 28-34）。

只有 PASSED / NEEDS_REVIEW / REJECTED 三种用户分区。内部记录候选 partition=extracted
（M-11）不是用户结果分区。review_type 保持有限集合，方便 M-13 筛选/批量/Deep Link。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.validation.contracts import (
    AllowedReviewAction,
    ReviewReason,
    ValidationIssue,
    ValidationPartition,
)

_STRICT = ConfigDict(extra="forbid")

# review_type 有限集合（模块需求 33）：方便 M-13 筛选/批量/Deep Link，不每种错误一个类型
REVIEW_TYPES = (
    "missing_required",
    "unresolved_conflict",
    "possible_duplicate",
    "low_confidence",
    "rule_mismatch",
    "invalid_format",
    "business_rule",
    "rejected",
)


class PartitionDecision(BaseModel):
    model_config = _STRICT

    partition: ValidationPartition
    review_type: str | None = None
    review_reason: ReviewReason | None = None
    allowed_actions: list[str] = []
    quality_contribution: dict = {}


class Partitioner:
    """验证层 issue 分组 → 最终分区。

    REJECTED：结构根本不满足 / 不可恢复必填缺失 / 证据无效 / 违反不可接受业务约束。
    NEEDS_REVIEW：可人工补全缺失 / 未裁决冲突 / 近似重复 / 低证据 / 规则失效。
    PASSED：全部 gate PASS + dedupe resolved + conflict resolved。
    """

    def decide(
        self,
        *,
        structural: list[ValidationIssue],
        required: list[ValidationIssue],
        evidence: list[ValidationIssue],
        business: list[ValidationIssue],
        dedupe_unresolved: bool,
        conflict_unresolved: bool,
    ) -> PartitionDecision:
        # 1) REJECTED：结构根本不满足 / 证据无效 / 不可接受业务约束
        if structural:
            return PartitionDecision(
                partition=ValidationPartition.REJECTED,
                review_type="invalid_format",
                review_reason=ReviewReason.INVALID_FORMAT,
                allowed_actions=[AllowedReviewAction.REJECT.value],
                quality_contribution={"rejected": True},
            )
        if self._evidence_invalid(evidence):
            return PartitionDecision(
                partition=ValidationPartition.REJECTED,
                review_type="rejected",
                review_reason=ReviewReason.INVALID_FORMAT,
                allowed_actions=[AllowedReviewAction.REJECT.value],
                quality_contribution={"rejected": True},
            )
        if self._fatal_business(business):
            return PartitionDecision(
                partition=ValidationPartition.REJECTED,
                review_type="business_rule",
                review_reason=ReviewReason.BUSINESS_RULE_FAILED,
                allowed_actions=[AllowedReviewAction.REJECT.value],
                quality_contribution={"rejected": True},
            )
        # 2) NEEDS_REVIEW：可人工补全缺失 / 未裁决冲突 / 近似重复 / 低证据 / 规则失效
        if required:
            return PartitionDecision(
                partition=ValidationPartition.NEEDS_REVIEW,
                review_type="missing_required",
                review_reason=ReviewReason.MISSING_REQUIRED,
                allowed_actions=[
                    AllowedReviewAction.EDIT.value,
                    AllowedReviewAction.APPROVE.value,
                    AllowedReviewAction.REJECT.value,
                ],
                quality_contribution={"missing_required": True},
            )
        if conflict_unresolved:
            return PartitionDecision(
                partition=ValidationPartition.NEEDS_REVIEW,
                review_type="unresolved_conflict",
                review_reason=ReviewReason.UNRESOLVED_CONFLICT,
                allowed_actions=[
                    AllowedReviewAction.RESOLVE_CONFLICT.value,
                    AllowedReviewAction.REJECT.value,
                ],
                quality_contribution={"conflict": True},
            )
        if dedupe_unresolved:
            return PartitionDecision(
                partition=ValidationPartition.NEEDS_REVIEW,
                review_type="possible_duplicate",
                review_reason=ReviewReason.POSSIBLE_DUPLICATE,
                allowed_actions=[
                    AllowedReviewAction.MERGE_DUPLICATE.value,
                    AllowedReviewAction.REJECT.value,
                ],
                quality_contribution={"duplicate": True},
            )
        if evidence:
            return PartitionDecision(
                partition=ValidationPartition.NEEDS_REVIEW,
                review_type="low_confidence",
                review_reason=ReviewReason.LOW_EVIDENCE_CONFIDENCE,
                allowed_actions=[
                    AllowedReviewAction.EDIT.value,
                    AllowedReviewAction.AGENT_REEVALUATE.value,
                    AllowedReviewAction.REJECT.value,
                ],
                quality_contribution={"low_evidence": True},
            )
        # 3) PASSED：全部 gate PASS + dedupe resolved + conflict resolved
        return PartitionDecision(
            partition=ValidationPartition.PASSED,
            review_type=None,
            review_reason=None,
            allowed_actions=[AllowedReviewAction.APPROVE.value],
            quality_contribution={"passed": True},
        )

    @staticmethod
    def _evidence_invalid(evidence: list[ValidationIssue]) -> bool:
        return any(
            i.code
            in {
                "EVIDENCE_OWNER_MISMATCH",
                "EVIDENCE_TASK_MISMATCH",
                "EVIDENCE_SPEC_MISMATCH",
                "EVIDENCE_NO_TRACE",
            }
            for i in evidence
        )

    @staticmethod
    def _fatal_business(business: list[ValidationIssue]) -> bool:
        return any(i.code == "BUSINESS_CONSTRAINT_VIOLATION" for i in business)


__all__ = ["Partitioner", "PartitionDecision", "REVIEW_TYPES"]
