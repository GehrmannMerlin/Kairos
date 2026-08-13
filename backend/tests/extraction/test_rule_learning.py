"""Rule learning: LLM proposes → representative validation → promote / threshold FAIL."""
from __future__ import annotations

import pytest
from app.extraction.rule_learning import RuleCandidate, RuleLearningService
from tests.extraction.conftest import seed_snapshot

PAGE_A = "<html><body><h1 class=\"company-name\">深圳光明科技</h1></body></html>"
PAGE_B = "<html><body><h1 class=\"company-name\">深圳南山科技</h1></body></html>"
PAGE_C = "<html><body><h1 class=\"company-name\">深圳福田科技</h1></body></html>"


@pytest.mark.asyncio
async def test_promote_rule_on_threshold_pass(ctx, storage):
    db = ctx["db"]
    snap_ids = [
        seed_snapshot(ctx, PAGE_A.encode("utf-8"), storage),
        seed_snapshot(ctx, PAGE_B.encode("utf-8"), storage),
        seed_snapshot(ctx, PAGE_C.encode("utf-8"), storage),
    ]
    values = ["深圳光明科技", "深圳南山科技", "深圳福田科技"]
    candidate = RuleCandidate(
        site_host="fixture.test",
        field_name="公司名",
        rule_type="css",
        selector="h1.company-name",
        value_transform="identity",
        samples=[
            {"snapshot_id": sid, "value": val, "quote": val}
            for sid, val in zip(snap_ids, values, strict=True)
        ],
    )
    service = RuleLearningService(db, storage, user_id=ctx["user"].id)
    result = await service.validate_representative(candidate)
    assert result.pass_threshold is True
    rule = service.promote(result, schema_valid=True)
    assert rule is not None
    assert rule.status == "ACTIVE"
    assert rule.version == 1
    assert rule.quality["precision"] >= 0.9
    assert rule.quality["coverage"] >= 0.5


@pytest.mark.asyncio
async def test_no_promote_on_threshold_fail(ctx, storage):
    db = ctx["db"]
    snap_ids = [
        seed_snapshot(ctx, PAGE_A.encode("utf-8"), storage),
        seed_snapshot(ctx, PAGE_B.encode("utf-8"), storage),
        seed_snapshot(ctx, PAGE_C.encode("utf-8"), storage),
    ]
    # wrong selector → coverage fails → no promotion
    candidate = RuleCandidate(
        site_host="fixture.test",
        field_name="公司名",
        rule_type="css",
        selector="div.missing",
        value_transform="identity",
        samples=[
            {"snapshot_id": sid, "value": v, "quote": v}
            for sid, v in zip(snap_ids, ["a", "b", "c"], strict=True)
        ],
    )
    service = RuleLearningService(db, storage, user_id=ctx["user"].id)
    result = await service.validate_representative(candidate)
    assert result.pass_threshold is False
    assert service.promote(result, schema_valid=True) is None
