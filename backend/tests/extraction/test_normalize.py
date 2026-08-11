"""NormalizeNodeExecutor — 只做字段级 canonicalization（四十五），不做业务去重/冲突。"""
from __future__ import annotations

import pytest
from app.extraction.context import ExtractionContextBuilder
from app.extraction.executor import ExtractNodeExecutor, NormalizeNodeExecutor
from app.extraction.llm import SemanticExtractionResult
from app.extraction.pipeline import ExtractionPipeline
from app.extraction.repository import ExtractionRepository
from tests.crawling.conftest import make_unit
from tests.extraction.conftest import seed_snapshot

HTML = """
<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization",
 "name":"深圳光明科技","url":"https://EXAMPLE.com/PATH"}</script>
</head><body><p>深圳光明科技简介</p></body></html>
"""


class _NoopAgent:
    async def extract(self, inp, resolved=None, api_key=None):
        return SemanticExtractionResult(fields=[])


@pytest.mark.asyncio
async def test_normalize_executor_canonicalizes_field_values(ctx, storage):
    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    seed_snapshot(ctx, HTML.encode(), storage)
    pipeline = ExtractionPipeline(
        db, storage,
        context_builder=ExtractionContextBuilder(db, storage),
        llm_agent=_NoopAgent(),
    )
    await ExtractNodeExecutor(db, storage, pipeline=pipeline).execute(make_unit(run, 1, "extract"))
    records = ExtractionRepository(db).records_for_task(user.id, ctx["task"].id)
    # extract 已 canonical（host 小写）
    assert records[0].payload["values"]["官网"] == "https://example.com/PATH"

    # 模拟未规范化数据：改回大写 host，再由 Normalize 节点做字段级 canonicalization
    records[0].payload["values"]["官网"] = "https://EXAMPLE.COM/PATH"
    db.commit()

    result = await NormalizeNodeExecutor(db).execute(make_unit(run, 2, "normalize"))
    assert result.status == "OK"
    assert result.committed_refs["normalized"] == 1
    db.refresh(records[0])
    assert records[0].payload["values"]["官网"] == "https://example.com/PATH"
