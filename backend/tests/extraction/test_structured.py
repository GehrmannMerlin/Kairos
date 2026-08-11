"""Fixture A unit: JSON-LD / Meta / Table deterministic extractors (LLM invocation = 0)."""
from __future__ import annotations

import pytest
from app.domain.models import CollectionSpecVersion, PageSnapshot
from app.domain.repository import SpecVersionRepository
from app.extraction.context import ExtractionContextBuilder
from app.extraction.contracts import ExtractorMethod
from app.extraction.structured import JsonLdExtractor, MetaExtractor, TableExtractor
from tests.extraction.conftest import collection_fields, seed_snapshot

HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization",
 "name":"深圳光明科技","url":"https://gm.example.com",
 "telephone":"0755-88886666","email":"contact@gm.example.com"}
</script>
<meta property="og:site_name" content="光明科技官网"/>
<meta name="description" content="主营自动化设备与工业机器人"/>
</head>
<body>
<h1>深圳光明科技</h1>
<table>
 <tr><th>电话</th><td>0755-88886666</td></tr>
 <tr><th>邮箱</th><td>contact@gm.example.com</td></tr>
 <tr><th>地址</th><td>深圳市南山区科技园</td></tr>
</table>
</body></html>
"""


@pytest.mark.asyncio
async def test_jsonld_extracts_canonical_fields(ctx, storage):
    db = ctx["db"]
    snap_id = seed_snapshot(ctx, HTML.encode("utf-8"), storage)
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(ctx["user"].id, ctx["task"].id, 1)
    builder = ExtractionContextBuilder(db, storage)
    ectx = await builder.build(snapshot, spec.payload)

    result = await JsonLdExtractor().extract(
        ectx, unresolved=[f["name"] for f in collection_fields()]
    )
    values = {c.field_name: c.raw_value for c in result.candidates}
    assert values["公司名"] == "深圳光明科技"
    assert values["官网"] == "https://gm.example.com"
    assert values["电话"] == "0755-88886666"
    assert values["邮箱"] == "contact@gm.example.com"
    assert all(c.method == ExtractorMethod.JSON_LD for c in result.candidates)
    assert "主营产品" in result.unresolved_fields  # JSON-LD has no description → unresolved
    assert all(c.source_locator for c in result.candidates)  # structured locator required


@pytest.mark.asyncio
async def test_meta_extracts_description(ctx, storage):
    db = ctx["db"]
    snap_id = seed_snapshot(ctx, HTML.encode("utf-8"), storage)
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(ctx["user"].id, ctx["task"].id, 1)
    ectx = await ExtractionContextBuilder(db, storage).build(snapshot, spec.payload)

    result = await MetaExtractor().extract(ectx, unresolved=["主营产品"])
    values = {c.field_name: c.raw_value for c in result.candidates}
    assert values["主营产品"] == "主营自动化设备与工业机器人"
    assert result.candidates[0].method == ExtractorMethod.META


@pytest.mark.asyncio
async def test_table_extracts_key_value_rows(ctx, storage):
    db = ctx["db"]
    fields = collection_fields() + [
        {"name": "地址", "type": "text", "required": False, "description": "公司地址"}
    ]
    spec_row = (
        db.query(CollectionSpecVersion)
        .filter(
            CollectionSpecVersion.user_id == ctx["user"].id,
            CollectionSpecVersion.task_id == ctx["task"].id,
            CollectionSpecVersion.version == 1,
        )
        .first()
    )
    spec_row.payload = {
        "fields": fields,
        "task_type": "SPECIFIED_SOURCE",
        "goal": "x",
        "source_scope": {},
        "completion_conditions": [],
        "advanced_settings": {},
    }
    db.commit()

    snap_id = seed_snapshot(ctx, HTML.encode("utf-8"), storage)
    snapshot = db.get(PageSnapshot, snap_id)
    ectx = await ExtractionContextBuilder(db, storage).build(snapshot, spec_row.payload)

    result = await TableExtractor().extract(ectx, unresolved=["地址", "主营产品"])
    values = {c.field_name: c.raw_value for c in result.candidates}
    assert values["地址"] == "深圳市南山区科技园"
    assert result.candidates[0].method == ExtractorMethod.TABLE
    assert "主营产品" in result.unresolved_fields  # no table row for it
