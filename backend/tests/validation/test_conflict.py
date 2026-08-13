"""CORE TEST C — Conflict（模块需求 71）。CASE 1：source priority + stronger evidence →
deterministic resolution。CASE 2：两边强度相近 → NEEDS_REVIEW 且不静默选值。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.validation.conflict import ConflictCandidateValue, ConflictResolver


def _cand(
    record_id,
    value,
    *,
    priority=60,
    strength=1.0,
    method="json_ld",
    rule_validated=False,
    fetched_at=None,
    confidence=0.9,
):
    return ConflictCandidateValue(
        record_id=record_id,
        value=value,
        evidence_strength=strength,
        source_priority=priority,
        method=method,
        rule_validated=rule_validated,
        fetched_at=fetched_at or datetime(2026, 8, 11, tzinfo=UTC),
        confidence=confidence,
    )


def test_source_priority_resolves_deterministically():
    r = ConflictResolver().resolve(
        "官网",
        [
            _cand(1, "https://seed.example.com", priority=100),  # USER_SEED 优先
            _cand(2, "https://search.example.org", priority=60),  # SEARCH_RESULT
        ],
    )
    assert r.decision == "resolved"
    assert r.chosen_value == "https://seed.example.com"
    assert r.rejected_refs[0]["record_id"] == 2  # 保留 rejected 审计


def test_stronger_evidence_resolves_within_same_source_tier():
    r = ConflictResolver().resolve(
        "电话",
        [
            _cand(1, "13800138000", strength=0.4, method="llm", confidence=0.4),  # LLM 低证据
            _cand(2, "13900139000", strength=1.0),  # json_ld 强证据
        ],
    )
    assert r.decision == "resolved"
    assert r.chosen_value == "13900139000"


def test_tie_goes_needs_review_and_keeps_all_values():
    r = ConflictResolver().resolve(
        "主营产品",
        [
            _cand(1, "产品A", strength=1.0, method="json_ld"),
            _cand(2, "产品B", strength=1.0, method="json_ld"),  # 同来源/同方法/同证据 → tie
        ],
    )
    assert r.decision == "needs_review"
    assert r.chosen_value is None  # 不静默选值
    assert len(r.rejected_refs) == 2  # 全部候选保留


def test_low_confidence_llm_loses_to_rule():
    r = ConflictResolver().resolve(
        "地址",
        [
            _cand(1, "addr-llm", method="llm", strength=0.5, confidence=0.3),
            _cand(2, "addr-rule", method="rule", strength=0.5, rule_validated=True),
        ],
    )
    assert r.decision == "resolved"
    assert r.chosen_value == "addr-rule"


def test_single_source_is_trivially_resolved():
    r = ConflictResolver().resolve("官网", [_cand(1, "https://only.com")])
    assert r.decision == "resolved"
    assert r.chosen_value == "https://only.com"
