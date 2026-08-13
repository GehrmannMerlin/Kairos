"""Extraction batch idempotency (三十九~四十): double-run no duplicates; no re-fetch."""
from __future__ import annotations

import pytest
from app.extraction.context import ExtractionContextBuilder
from app.extraction.executor import ExtractNodeExecutor
from app.extraction.llm import SemanticExtractionResult
from app.extraction.pipeline import ExtractionPipeline
from app.extraction.repository import ExtractionRepository, FieldEvidenceRepository
from tests.crawling.conftest import make_unit
from tests.extraction.conftest import seed_snapshot

HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization",
 "name":"深圳光明科技","url":"https://gm.example.com",
 "telephone":"0755-88886666","email":"contact@gm.example.com"}
</script>
<meta name="description" content="主营工业自动化设备"/>
</head><body></body></html>
"""


class _NoopAgent:
    """Never calls the model; the fixture resolves all fields deterministically."""

    async def extract(self, inp, resolved=None, api_key=None):
        return SemanticExtractionResult(fields=[])


def _executor(ctx, storage) -> ExtractNodeExecutor:
    pipeline = ExtractionPipeline(
        ctx["db"],
        storage,
        context_builder=ExtractionContextBuilder(ctx["db"], storage),
        llm_agent=_NoopAgent(),
    )
    return ExtractNodeExecutor(ctx["db"], storage, pipeline=pipeline)


@pytest.mark.asyncio
async def test_double_run_produces_no_duplicate_candidates_or_evidence(ctx, storage):
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    seed_snapshot(ctx, HTML.encode("utf-8"), storage)
    executor = _executor(ctx, storage)

    r1 = await executor.execute(make_unit(run, 1, "extract"))
    assert r1.status == "OK"
    assert r1.committed_refs["extracted"] == 1
    records = ExtractionRepository(db).records_for_task(user.id, task.id)
    assert len(records) == 1
    evidence = FieldEvidenceRepository(db).list_for_record(user.id, records[0].id)
    assert len(evidence) == 5  # 公司名/官网/电话/邮箱/主营产品

    r2 = await executor.execute(make_unit(run, 2, "extract"))
    assert r2.status == "OK"
    assert r2.committed_refs["extracted"] == 0  # already extracted → skip
    assert len(ExtractionRepository(db).records_for_task(user.id, task.id)) == 1
    assert len(FieldEvidenceRepository(db).list_for_record(user.id, records[0].id)) == 5
