"""M-11 完成门禁三类 fixture（A structured / B site rule / C LLM fallback）+ M-10 handoff。"""
from __future__ import annotations

import pytest
from app.domain.models import CollectionSpecVersion, PageSnapshot
from app.domain.repository import SpecVersionRepository
from app.extraction.context import ExtractionContextBuilder
from app.extraction.executor import ExtractNodeExecutor
from app.extraction.llm import SemanticExtractionResult, SemanticFieldCandidate
from app.extraction.pipeline import ExtractionPipeline
from app.extraction.repository import (
    ExtractionRepository,
    ExtractorRuleRepository,
    FieldEvidenceRepository,
)
from app.extraction.site_rules import SiteRuleExtractor
from tests.crawling.conftest import make_unit
from tests.extraction.conftest import seed_snapshot

# ---- Fixture A：结构化页面，LLM invocation = 0 ----
STRUCTURED_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization",
 "name":"深圳光明科技","url":"https://gm.example.com",
 "telephone":"0755-88886666","email":"contact@gm.example.com"}
</script>
<meta property="og:site_name" content="光明科技官网"/>
<meta name="description" content="主营自动化设备与工业机器人"/>
</head>
<body><h1>深圳光明科技</h1>
<table><tr><th>电话</th><td>0755-88886666</td></tr></table>
</body></html>
"""


@pytest.mark.asyncio
async def test_fixture_a_structured_no_llm(ctx, storage):
    db = ctx["db"]
    user = ctx["user"]
    task = ctx["task"]
    run = ctx["run"]
    snap_id = seed_snapshot(ctx, STRUCTURED_HTML.encode("utf-8"), storage)
    assert snap_id

    llm_invocations = {"n": 0}

    class CountingAgent:
        async def extract(self, inp, resolved=None, api_key=None):
            llm_invocations["n"] += 1
            return SemanticExtractionResult(fields=[])

    pipeline = ExtractionPipeline(
        db, storage,
        context_builder=ExtractionContextBuilder(db, storage),
        llm_agent=CountingAgent(),
    )
    executor = ExtractNodeExecutor(db, storage, pipeline=pipeline)
    result = await executor.execute(make_unit(run, 1, "extract"))

    assert result.status == "OK"
    assert result.committed_refs["extracted"] == 1
    assert llm_invocations["n"] == 0
    records = ExtractionRepository(db).records_for_task(user.id, task.id)
    assert len(records) == 1
    payload = records[0].payload
    assert payload["values"]["公司名"] == "深圳光明科技"
    assert payload["values"]["官网"] == "https://gm.example.com"
    assert payload["values"]["电话"] == "075588886666"  # schema-level normalized phone
    evidence = FieldEvidenceRepository(db).list_for_record(user.id, records[0].id)
    fields = {e.field_name for e in evidence}
    assert {"公司名", "官网", "电话", "邮箱", "主营产品"} <= fields
    for e in evidence:
        assert e.snapshot_id == snap_id
        assert e.source_locator
        assert e.raw_snippet
        assert e.extract_method in ("json_ld", "meta", "table")
        assert e.extractor_version
        assert e.confidence is not None


# ---- Fixture B：站点规则验证后提取（LLM = 0）+ 回滚 ----
RULE_PAGE = """
<html><body>
<header><h1 class="company-name">模板科技有限公司</h1></header>
<div class="contact">
  <p class="biz">主营自动化设备与工业机器人</p>
</div>
</body></html>
"""


def _set_spec_fields(db, user_id: int, task_id: int, fields: list[dict]) -> None:
    spec_row = (
        db.query(CollectionSpecVersion)
        .filter(
            CollectionSpecVersion.user_id == user_id,
            CollectionSpecVersion.task_id == task_id,
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


@pytest.mark.asyncio
async def test_fixture_b_site_rule_after_validation_no_llm(ctx, storage):
    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    task = ctx["task"]
    _set_spec_fields(
        db,
        user.id,
        task.id,
        [
            {"name": "公司名", "type": "text", "required": True},
            {"name": "主营产品", "type": "text", "required": False},
        ],
    )
    rule_repo = ExtractorRuleRepository(db)
    rule_repo.create(
        user_id=user.id, site_host="fixture.test", field_name="公司名",
        schema_identity="name", rule_type="css", selector="h1.company-name",
        value_transform="identity", version=1, status="ACTIVE",
        quality={
            "precision": 1.0,
            "coverage": 1.0,
            "samples": 3,
            "validated_snapshot_ids": [1, 2, 3],
        },
    )
    rule_repo.create(
        user_id=user.id, site_host="fixture.test", field_name="主营产品",
        schema_identity="description", rule_type="css", selector="p.biz",
        value_transform="identity", version=1, status="ACTIVE",
    )
    db.commit()

    seed_snapshot(ctx, RULE_PAGE.encode("utf-8"), storage)
    llm_invocations = {"n": 0}

    class CountingAgent:
        async def extract(self, inp, resolved=None, api_key=None):
            llm_invocations["n"] += 1
            return SemanticExtractionResult(fields=[])

    pipeline = ExtractionPipeline(
        db, storage,
        context_builder=ExtractionContextBuilder(db, storage),
        llm_agent=CountingAgent(),
    )
    result = await ExtractNodeExecutor(db, storage, pipeline=pipeline).execute(
        make_unit(run, 1, "extract")
    )
    assert result.committed_refs["extracted"] == 1
    assert llm_invocations["n"] == 0
    records = ExtractionRepository(db).records_for_task(user.id, task.id)
    ev = FieldEvidenceRepository(db).list_for_record(user.id, records[0].id)
    company_ev = next(e for e in ev if e.field_name == "公司名")
    assert company_ev.extract_method == "rule"
    assert company_ev.rule_version_id == 1
    assert company_ev.source_locator == "css:h1.company-name"


@pytest.mark.asyncio
async def test_fixture_b_rollback_v2_bad_rule_uses_v1(ctx, storage):
    from app.extraction.rule_learning import RuleLearningService

    db = ctx["db"]
    user = ctx["user"]
    repo = ExtractorRuleRepository(db)
    repo.create(
        user_id=user.id, site_host="fixture.test", field_name="公司名",
        schema_identity="name", rule_type="css", selector="h1.company-name",
        value_transform="identity", version=1, status="ACTIVE",
    )
    repo.create(
        user_id=user.id, site_host="fixture.test", field_name="公司名",
        schema_identity="name", rule_type="css", selector="div.broken",
        value_transform="identity", version=2, status="ACTIVE",
    )
    db.commit()
    service = RuleLearningService(db, storage, user_id=user.id)
    service.rollback(site_host="fixture.test", field_name="公司名", to_version=1)
    db.commit()

    snap_id = seed_snapshot(ctx, RULE_PAGE.encode("utf-8"), storage)
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(user.id, ctx["task"].id, 1)
    ectx = await ExtractionContextBuilder(db, storage).build(snapshot, spec.payload)
    result = await SiteRuleExtractor(db).extract(ectx, unresolved=["公司名"])
    assert {c.field_name: c.raw_value for c in result.candidates}["公司名"] == "模板科技有限公司"
    assert result.candidates[0].rule_version == 1  # 回退到 v1


# ---- Fixture C：LLM fallback（只发送 unresolved + 证据接地）----
IRREGULAR_HTML = """
<html><body><div class="main">
  <script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization",
    "name":"深圳南山科技有限公司","url":"https://nanshan.example.com",
    "telephone":"0755-33334444","email":"hr@nanshan.example.com"}</script>
  <p>深圳南山科技有限公司成立于2010年。</p>
  <p>公司主营工业自动化设备与机器人集成。</p>
</div></body></html>
"""


@pytest.mark.asyncio
async def test_fixture_c_llm_fallback_only_unresolved_grounded(ctx, storage):
    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    snap_id = seed_snapshot(ctx, IRREGULAR_HTML.encode(), storage)

    sent: list[str] = []

    class FakeAgent:
        async def extract(self, inp, resolved=None, api_key=None):
            sent.extend(inp.unresolved_fields)
            return SemanticExtractionResult(
                fields=[
                    SemanticFieldCandidate(
                        field_name="主营产品",
                        value="工业自动化设备与机器人集成",
                        evidence_quote="公司主营工业自动化设备与机器人集成",
                        confidence=0.85,
                    )
                ]
            )

    pipeline = ExtractionPipeline(
        db, storage,
        context_builder=ExtractionContextBuilder(db, storage),
        llm_agent=FakeAgent(),
    )
    result = await ExtractNodeExecutor(db, storage, pipeline=pipeline).execute(
        make_unit(run, 1, "extract")
    )
    assert result.committed_refs["extracted"] == 1
    records = ExtractionRepository(db).records_for_task(user.id, ctx["task"].id)
    payload = records[0].payload
    assert payload["values"]["主营产品"] == "工业自动化设备与机器人集成"
    assert sent == ["主营产品"]  # 只发送 unresolved 字段
    ev = FieldEvidenceRepository(db).list_for_record(user.id, records[0].id)
    product_ev = next(e for e in ev if e.field_name == "主营产品")
    assert product_ev.extract_method == "llm"
    assert product_ev.snapshot_id == snap_id
    assert product_ev.confidence is not None


# ---- D-072：minimal snippet 不依赖 raw snapshot 永久存在 ----
@pytest.mark.asyncio
async def test_fixture_evidence_survives_snapshot_deletion(ctx, storage):
    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    seed_snapshot(ctx, STRUCTURED_HTML.encode("utf-8"), storage)
    snapshot = db.query(PageSnapshot).first()
    pipeline = ExtractionPipeline(
        db, storage, context_builder=ExtractionContextBuilder(db, storage)
    )
    await ExtractNodeExecutor(db, storage, pipeline=pipeline).execute(
        make_unit(run, 1, "extract")
    )

    # 模拟重型文件生命周期清理删除对象存储中的原始内容
    storage._objects = {}
    records = ExtractionRepository(db).records_for_task(user.id, ctx["task"].id)
    ev = FieldEvidenceRepository(db).list_for_record(user.id, records[0].id)
    company = next(e for e in ev if e.field_name == "公司名")
    assert company.raw_snippet  # bounded snippet 仍保留
    assert company.source_locator
    assert company.snapshot_id == snapshot.id
    assert company.evidence_hash


# ---- M-10 → M-11 handoff：READY_FOR_FETCH → Fetch → Snapshot → Extract ----
@pytest.mark.asyncio
async def test_m10_to_m11_handoff_fetch_then_extract(ctx, storage):
    from app.crawling.fetch_executor import FetchNodeExecutor
    from app.crawling.http_fetch import SafeFetchHttp
    from app.crawling.repository import PageSnapshotRepository
    from app.discovery.http import DiscoveryHttp
    from app.discovery.robots import RobotsCache
    from tests.crawling.conftest import SITE_HOST, FakeFetchTransport, seed_ready

    db = ctx["db"]
    user = ctx["user"]
    run = ctx["run"]
    body = (
        '<html><head><script type="application/ld+json">'
        '{"name":"深圳光明科技"}</script></head>'
        '<body><p>深圳光明科技公司介绍</p></body></html>'
    ).encode()
    transport = FakeFetchTransport(
        {
            "/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
            "/": {"status": 200, "content_type": "text/html", "body": body},
        }
    )
    http = SafeFetchHttp(transport=transport, allow_hosts=frozenset({SITE_HOST}))
    robots = RobotsCache(DiscoveryHttp(transport=transport, allow_hosts=frozenset({SITE_HOST})))
    seed_ready(ctx, f"http://{SITE_HOST}/")

    fetch = FetchNodeExecutor(db, http=http, robots=robots, storage=storage, retry_base_seconds=0)
    fetch_result = await fetch.execute(make_unit(run, 1, "fetch"))
    assert fetch_result.status == "OK"
    assert fetch_result.committed_refs["fetched"] == 1
    snapshots = PageSnapshotRepository(db).list_for_task(user.id, ctx["task"].id)
    assert len(snapshots) == 1

    class _NoopAgent:
        async def extract(self, inp, resolved=None, api_key=None):
            return SemanticExtractionResult(fields=[])

    pipeline = ExtractionPipeline(
        db, storage,
        context_builder=ExtractionContextBuilder(db, storage),
        llm_agent=_NoopAgent(),
    )
    extract = ExtractNodeExecutor(db, storage, pipeline=pipeline)
    extract_result = await extract.execute(make_unit(run, 2, "extract"))
    assert extract_result.status == "OK"
    assert extract_result.committed_refs["extracted"] == 1
    records = ExtractionRepository(db).records_for_task(user.id, ctx["task"].id)
    assert records[0].payload["values"]["公司名"] == "深圳光明科技"


# ---- DEPLOY-GATE-3: rendered SPA readable text must not be lost to the 30KB HTML window ----
# 回归：Task 149（toutiao.com 动态渲染）证明 542KB rendered DOM 的新闻标题位于
# ~503KB 偏移（大段 script 启动包之后）。若 readable_text 只从 max_context_bytes(30KB)
# 的 HTML 窗口提取，将只剩空壳（"今日头条"）→ LLM 看到 4 字符 → 0 records。
# 修复契约：readable_text 必须基于完整 HTML 提取，再各自截断到 max_context_chars；
# html=bounded_html 给确定性提取器的 30KB 上限保持不变。
RENDERED_SPA_HTML = (
    "<html><head><title>今日头条</title>"
    + "<script>" + ("window.__BOOT_DATA__ = '{}';" * 1) + "</script>"
    + ("<script>" + ("x" * 5000) + "</script>") * 8  # ~40KB script boot blocks
    + "</head><body>"
    + '<div class="feed">'
    + '<a class="title" aria-label="联播+｜太平洋彼岸值得信赖的朋友">'
    + "联播+｜太平洋彼岸值得信赖的朋友</a>"
    + '<a class="title" aria-label="公积金新政来了，有哪些利好？">公积金新政来了，有哪些利好？</a>'
    + "</div></body></html>"
)


@pytest.mark.asyncio
async def test_rendered_spa_readable_text_survives_large_boot_block(ctx, storage):
    """Replay of Task 149: readable_text must include deep-offset real content,
    not just the first 30KB HTML window (shell)."""
    db = ctx["db"]
    user = ctx["user"]
    spec = SpecVersionRepository(db).get_version(user.id, ctx["task"].id, 1)
    snap_id = seed_snapshot(ctx, RENDERED_SPA_HTML.encode("utf-8"), storage)

    builder = ExtractionContextBuilder(db, storage)
    ectx = await builder.build(db.get(PageSnapshot, snap_id), spec.payload)

    # The raw HTML is well over max_context_bytes (boot block pushes content deep)
    assert len(RENDERED_SPA_HTML) > 30_000
    # Real content must survive into readable_text for the LLM
    assert "联播+｜太平洋彼岸值得信赖的朋友" in ectx.readable_text
    assert "公积金新政来了，有哪些利好？" in ectx.readable_text
    # Deterministic extractors still get a bounded html window (safety cap preserved)
    assert len(ectx.html) <= 30_000
