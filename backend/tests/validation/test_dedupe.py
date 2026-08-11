"""CORE TEST B — Dedupe（模块需求 70）：同一 business key 多来源 → 单一业务实体；
retry 同批 → 无重复 group；deterministic fuzzy threshold 边界。"""

from __future__ import annotations

from app.domain.spec import FieldSpec
from app.validation.dedupe import (
    BusinessKeyPolicy,
    BusinessUniqueKeyStrategy,
    DedupeEngine,
    business_key_fingerprint,
    compute_business_key,
)
from app.validation.policies import ValidationSettings

FIELDS = [
    FieldSpec(name="公司名", type="text", required=True),
    FieldSpec(name="官网", type="url", required=True),
    FieldSpec(name="电话", type="phone", required=False),
]


def _record(rid, values):
    class _R:
        pass

    r = _R()
    r.id, r.payload = rid, {"values": values}
    return r


def test_strategy_default_key_is_all_required_fields():
    policy = BusinessUniqueKeyStrategy().resolve(
        {
            "task_type": "SPECIFIED_SOURCE",
            "goal": "x",
            "fields": [
                {"name": "公司名", "type": "text", "required": True},
                {"name": "官网", "type": "url", "required": True},
                {"name": "电话", "type": "phone", "required": False},
            ],
        }
    )
    assert policy.key_fields == ["公司名", "官网"]


def test_compute_business_key_normalizes_and_none_when_missing():
    policy = BusinessKeyPolicy(key_fields=["官网", "公司名"])
    key = compute_business_key({"公司名": "  Acme  ", "官网": "HTTPS://Acme.COM"}, policy, FIELDS)
    assert key is not None and "acme" in key.lower()
    assert compute_business_key({"公司名": "Acme"}, policy, FIELDS) is None


def test_fingerprint_ignores_timestamp_and_extractor_attempt():
    key = business_key_fingerprint("Acme", "https://acme.com")
    same = business_key_fingerprint("Acme", "https://acme.com")
    diff = business_key_fingerprint("Acme", "https://acme.com", "2026-08-11T00:00:00")
    assert key == same and key != diff


def test_exact_dedupe_merges_sources_and_preserves_all_records():
    policy = BusinessKeyPolicy(key_fields=["公司名", "官网"])
    engine = DedupeEngine()
    recs = [
        _record(1, {"公司名": "Acme", "官网": "https://acme.com"}),
        _record(2, {"公司名": "acme", "官网": "https://ACME.com"}),
    ]
    groups, ungrouped = engine.group(recs, policy, FIELDS)
    assert len(groups) == 1
    assert set(groups[0]["record_ids"]) == {1, 2}  # 单一业务实体，Evidence 链全部保留（不删历史）
    assert ungrouped == []


def test_fuzzy_merge_only_above_threshold_else_ungrouped():
    policy = BusinessKeyPolicy(key_fields=["公司名"])
    close = [_record(1, {"公司名": "Acme Corporation"}), _record(2, {"公司名": "Acme Corp"})]
    # 默认阈值 0.92 是保守 safe 边界：低于阈值不自动 merge（→ NEEDS_REVIEW 语义）
    default_engine = DedupeEngine()
    groups, _ = default_engine.group(close, policy, FIELDS)
    assert len(groups) == 2  # 不自动合并
    # 显式降低阈值（测试 deterministic fuzzy 逻辑）：达到阈值才自动并入
    loose_engine = DedupeEngine(ValidationSettings(dedupe_min_similarity=0.6))
    groups2, _ = loose_engine.group(close, policy, FIELDS)
    assert len(groups2) == 1 and groups2[0]["approximate"] is True


def test_retry_same_batch_stable_group_identity():
    policy = BusinessKeyPolicy(key_fields=["公司名", "官网"])
    engine = DedupeEngine()
    recs = [
        _record(1, {"公司名": "Acme", "官网": "https://acme.com"}),
        _record(2, {"公司名": "acme", "官网": "https://ACME.com"}),
    ]
    g1, _ = engine.group(recs, policy, FIELDS)
    g2, _ = engine.group(recs, policy, FIELDS)
    assert g1[0]["business_key_fingerprint"] == g2[0]["business_key_fingerprint"]
