"""三分区语义（模块需求 28-31）：PASSED / NEEDS_REVIEW / REJECTED 精确判定。"""

from __future__ import annotations

from app.validation.contracts import ValidationIssue
from app.validation.partitioner import Partitioner


def _issue(code, field_name=None):
    return ValidationIssue(code=code, field_name=field_name, detail=code)


def test_valid_record_partition_passed():
    d = Partitioner().decide(
        structural=[],
        required=[],
        evidence=[],
        business=[],
        dedupe_unresolved=False,
        conflict_unresolved=False,
    )
    assert d.partition.value == "passed"
    assert d.allowed_actions == ["approve"]


def test_missing_required_repairable_is_needs_review():
    d = Partitioner().decide(
        structural=[],
        required=[_issue("REQUIRED_FIELD_MISSING", "官网")],
        evidence=[],
        business=[],
        dedupe_unresolved=False,
        conflict_unresolved=False,
    )
    assert d.partition.value == "needs_review"
    assert d.review_type == "missing_required"
    assert "edit" in d.allowed_actions and "approve" in d.allowed_actions


def test_structural_failure_is_rejected():
    d = Partitioner().decide(
        structural=[_issue("SCHEMA_TYPE_URL", "官网")],
        required=[],
        evidence=[],
        business=[],
        dedupe_unresolved=False,
        conflict_unresolved=False,
    )
    assert d.partition.value == "rejected"
    assert d.review_reason.value == "invalid_format"


def test_evidence_invalid_is_rejected():
    d = Partitioner().decide(
        structural=[],
        required=[],
        evidence=[_issue("EVIDENCE_OWNER_MISMATCH", "官网")],
        business=[],
        dedupe_unresolved=False,
        conflict_unresolved=False,
    )
    assert d.partition.value == "rejected"
    assert d.allowed_actions == ["reject"]


def test_unresolved_conflict_is_needs_review():
    d = Partitioner().decide(
        structural=[],
        required=[],
        evidence=[],
        business=[],
        dedupe_unresolved=False,
        conflict_unresolved=True,
    )
    assert d.partition.value == "needs_review"
    assert d.review_type == "unresolved_conflict"
    assert "resolve_conflict" in d.allowed_actions


def test_possible_duplicate_is_needs_review():
    d = Partitioner().decide(
        structural=[],
        required=[],
        evidence=[],
        business=[],
        dedupe_unresolved=True,
        conflict_unresolved=False,
    )
    assert d.partition.value == "needs_review"
    assert d.review_type == "possible_duplicate"
    assert "merge_duplicate" in d.allowed_actions


def test_low_confidence_evidence_is_needs_review():
    d = Partitioner().decide(
        structural=[],
        required=[],
        evidence=[_issue("EVIDENCE_MISSING", "电话")],
        business=[],
        dedupe_unresolved=False,
        conflict_unresolved=False,
    )
    assert d.partition.value == "needs_review"
    assert d.review_type == "low_confidence"
    assert "agent_reevaluate" in d.allowed_actions


def test_no_fourth_partition():
    from app.validation.contracts import ValidationPartition

    assert {p.value for p in ValidationPartition} == {"passed", "needs_review", "rejected"}
