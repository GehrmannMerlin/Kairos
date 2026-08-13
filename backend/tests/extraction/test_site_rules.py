"""Fixture B unit: validated CSS/XPath site rules (LLM invocation = 0) + rollback."""
from __future__ import annotations

import pytest
from app.domain.models import PageSnapshot
from app.domain.repository import SpecVersionRepository
from app.extraction.context import ExtractionContextBuilder
from app.extraction.contracts import ExtractorMethod
from app.extraction.repository import ExtractorRuleRepository
from app.extraction.site_rules import SiteRuleExtractor
from tests.extraction.conftest import seed_snapshot

RULE_PAGE = """
<html><body>
<header><h1 class="company-name">模板科技有限公司</h1></header>
<div class="contact">
  <span class="tel">0755-99990000</span>
  <span class="mail">hr@template.example.com</span>
</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_site_rule_css_extracts_without_llm(ctx, storage):
    db = ctx["db"]
    user = ctx["user"]
    rule_repo = ExtractorRuleRepository(db)
    rule_repo.create(
        user_id=user.id,
        site_host="fixture.test",
        field_name="公司名",
        schema_identity="name",
        rule_type="css",
        selector="h1.company-name",
        value_transform="identity",
        version=1,
        status="ACTIVE",
        quality={
            "precision": 1.0,
            "coverage": 1.0,
            "samples": 3,
            "validated_snapshot_ids": [1, 2, 3],
        },
    )
    db.commit()

    snap_id = seed_snapshot(ctx, RULE_PAGE.encode("utf-8"), storage)
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(user.id, ctx["task"].id, 1)
    ectx = await ExtractionContextBuilder(db, storage).build(snapshot, spec.payload)

    result = await SiteRuleExtractor(db).extract(ectx, unresolved=["公司名", "电话"])
    values = {c.field_name: c.raw_value for c in result.candidates}
    assert values["公司名"] == "模板科技有限公司"
    assert result.candidates[0].method == ExtractorMethod.RULE
    assert result.candidates[0].rule_version == 1
    assert result.candidates[0].source_locator == "css:h1.company-name"
    assert "电话" in result.unresolved_fields  # no active rule for 电话


@pytest.mark.asyncio
async def test_site_rule_xpath_and_transform(ctx, storage):
    db = ctx["db"]
    user = ctx["user"]
    ExtractorRuleRepository(db).create(
        user_id=user.id,
        site_host="fixture.test",
        field_name="电话",
        schema_identity="telephone",
        rule_type="xpath",
        selector="//span[contains(@class,'tel')]/text()",
        value_transform="strip",
        version=1,
        status="ACTIVE",
    )
    db.commit()

    snap_id = seed_snapshot(ctx, RULE_PAGE.encode("utf-8"), storage)
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(user.id, ctx["task"].id, 1)
    ectx = await ExtractionContextBuilder(db, storage).build(snapshot, spec.payload)

    result = await SiteRuleExtractor(db).extract(ectx, unresolved=["电话"])
    assert {c.field_name: c.raw_value for c in result.candidates}["电话"] == "0755-99990000"
    assert result.candidates[0].source_locator == "xpath://span[contains(@class,'tel')]/text()"


@pytest.mark.asyncio
async def test_rule_mismatch_marks_failure_and_unresolved(ctx, storage):
    db = ctx["db"]
    user = ctx["user"]
    rule = ExtractorRuleRepository(db).create(
        user_id=user.id,
        site_host="fixture.test",
        field_name="公司名",
        schema_identity="name",
        rule_type="css",
        selector="div.gone",
        value_transform="identity",
        version=1,
        status="ACTIVE",
    )
    db.commit()

    snap_id = seed_snapshot(ctx, RULE_PAGE.encode("utf-8"), storage)
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(user.id, ctx["task"].id, 1)
    ectx = await ExtractionContextBuilder(db, storage).build(snapshot, spec.payload)

    result = await SiteRuleExtractor(db).extract(ectx, unresolved=["公司名"])
    assert result.candidates == []
    assert "公司名" in result.unresolved_fields
    db.commit()
    db.refresh(rule)
    assert rule.failure_count == 1
