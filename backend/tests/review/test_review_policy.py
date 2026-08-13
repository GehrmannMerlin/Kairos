"""M-13 ReviewPolicy：allowed_actions 派生 + 批量语义兼容。"""

from __future__ import annotations

import pytest
from app.review.policy import BatchCompatibilityError, ReviewPolicy


class _Rec:
    def __init__(
        self, partition: str, review_type: str | None = None, review_reason: str | None = None
    ):
        self.partition = partition
        self.review_type = review_type
        self.review_reason = review_reason


def test_passed_has_no_review_actions() -> None:
    assert ReviewPolicy.allowed_actions(record=_Rec("passed")) == []


def test_rejected_has_no_review_actions() -> None:
    assert ReviewPolicy.allowed_actions(record=_Rec("rejected")) == []


def test_needs_review_offers_core_actions() -> None:
    actions = ReviewPolicy.allowed_actions(record=_Rec("needs_review", "missing_required"))
    assert {"edit", "approve", "reject", "agent_reevaluate"}.issubset(actions)


def test_unresolved_conflict_offers_resolve() -> None:
    actions = ReviewPolicy.allowed_actions(record=_Rec("needs_review", "unresolved_conflict"))
    assert "resolve_conflict" in actions


def test_possible_duplicate_offers_merge() -> None:
    actions = ReviewPolicy.allowed_actions(record=_Rec("needs_review", "possible_duplicate"))
    assert "merge_duplicate" in actions


def test_batch_approve_requires_same_reason() -> None:
    rows = [
        _Rec("needs_review", "missing_required", "missing_required"),
        _Rec("needs_review", "low_evidence_confidence", "low_evidence_confidence"),
    ]
    with pytest.raises(BatchCompatibilityError):
        ReviewPolicy.assert_batch_compatible(action="approve", records=rows)


def test_batch_approve_same_reason_ok() -> None:
    rows = [
        _Rec("needs_review", "missing_required", "missing_required"),
        _Rec("needs_review", "missing_required", "missing_required"),
    ]
    ReviewPolicy.assert_batch_compatible(action="approve", records=rows)  # 不抛异常


def test_batch_reject_not_gated_by_reason() -> None:
    rows = [
        _Rec("needs_review", "missing_required", "missing_required"),
        _Rec("needs_review", "low_evidence_confidence", "low_evidence_confidence"),
    ]
    ReviewPolicy.assert_batch_compatible(action="reject", records=rows)  # 不抛异常
