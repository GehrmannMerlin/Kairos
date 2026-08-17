"""ExtractionPipeline ladder: structured → site rules → LLM fallback (field-level)."""

from __future__ import annotations

import pytest
from app.domain.models import PageSnapshot
from app.domain.repository import SpecVersionRepository
from app.extraction.context import ExtractionContextBuilder
from app.extraction.contracts import ExtractorMethod
from app.extraction.llm import SemanticExtractionResult, SemanticFieldCandidate
from app.extraction.pipeline import ExtractionPipeline
from tests.extraction.conftest import seed_snapshot

HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization",
 "name":"深圳光明科技","url":"https://gm.example.com",
 "telephone":"0755-88886666","email":"contact@gm.example.com"}
</script>
</head>
<body><div class="business">主营工业自动化设备与工业机器人</div></body></html>
"""


class FakeSemanticAgent:
    def __init__(self) -> None:
        self.invocation_count = 0
        self.sent_unresolved: list[str] | None = None

    async def extract(self, inp, resolved=None, api_key=None):
        self.invocation_count += 1
        self.sent_unresolved = list(inp.unresolved_fields)
        return SemanticExtractionResult(
            fields=[
                SemanticFieldCandidate(
                    field_name="主营产品",
                    value="工业自动化设备与工业机器人",
                    evidence_quote="主营工业自动化设备与工业机器人",
                    confidence=0.8,
                )
            ]
        )


@pytest.mark.asyncio
async def test_ladder_only_llm_falls_back_on_unresolved(ctx, storage):
    db = ctx["db"]
    user = ctx["user"]
    snap_id = seed_snapshot(ctx, HTML.encode("utf-8"), storage)
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(user.id, ctx["task"].id, 1)
    fake = FakeSemanticAgent()
    pipeline = ExtractionPipeline(
        db, storage, context_builder=ExtractionContextBuilder(db, storage), llm_agent=fake
    )
    result = await pipeline.run(snapshot, spec.payload, user_id=user.id)

    values = {c.field_name: c.raw_value for c in result.candidates}
    assert values["公司名"] == "深圳光明科技"  # JSON-LD
    assert values["官网"] == "https://gm.example.com"  # JSON-LD
    assert values["电话"] == "0755-88886666"  # JSON-LD
    assert values["邮箱"] == "contact@gm.example.com"  # JSON-LD
    assert values["主营产品"] == "工业自动化设备与工业机器人"  # LLM
    assert fake.invocation_count == 1
    assert fake.sent_unresolved == ["主营产品"]  # 字段级 fallback：只发 unresolved
    assert result.unresolved_fields == []
    # 每个有效候选都有 evidence 链
    for c in result.candidates:
        assert c.source_locator or c.method == ExtractorMethod.LLM
        assert c.raw_snippet


@pytest.mark.asyncio
async def test_llm_url_field_does_not_require_body_text_grounding(ctx, storage) -> None:
    """DEPLOY-GATE-3 上海政府：原文URL 等 URL 字段的值是页面自身 URL，不在正文中。
    文本 grounding 不应拦截 URL 字段（URL 格式校验已防幻觉）；非 URL 字段仍强制 grounding。"""
    db = ctx["db"]
    user = ctx["user"]
    body = "<html><body><p>这是一段页面正文，其中没有 URL。</p></body></html>".encode()
    snap_id = seed_snapshot(ctx, body, storage, url="https://gm.example.com/page")
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(user.id, ctx["task"].id, 1)

    class _UrlAgent:
        async def extract(self, inp, resolved=None, api_key=None):  # noqa: ARG002
            return SemanticExtractionResult(
                fields=[
                    SemanticFieldCandidate(
                        field_name="官网",  # URL 字段，页面自身 URL 不在正文
                        value="https://gm.example.com/page",
                        evidence_quote="https://gm.example.com/page",
                        confidence=0.9,
                    )
                ]
            )

    pipeline = ExtractionPipeline(
        db,
        storage,
        context_builder=ExtractionContextBuilder(db, storage),
        llm_agent=_UrlAgent(),
    )
    result = await pipeline.run(snapshot, spec.payload, user_id=user.id)

    url_cands = [c for c in result.candidates if c.field_name == "官网"]
    assert len(url_cands) == 1
    assert url_cands[0].raw_value == "https://gm.example.com/page"
    assert "官网" not in result.unresolved_fields
    assert not any(
        i.code == "EVIDENCE_NOT_GROUNDED" and i.field_name == "官网" for i in result.issues
    )


@pytest.mark.asyncio
async def test_structured_fixture_uses_no_llm(ctx, storage):
    db = ctx["db"]
    user = ctx["user"]
    snap_id = seed_snapshot(ctx, HTML.encode("utf-8"), storage)
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(user.id, ctx["task"].id, 1)
    fake = FakeSemanticAgent()
    pipeline = ExtractionPipeline(
        db, storage, context_builder=ExtractionContextBuilder(db, storage), llm_agent=fake
    )
    spec.payload = {
        **spec.payload,
        "fields": [
            {"name": "公司名", "type": "text", "required": True},
            {"name": "官网", "type": "url", "required": True},
        ],
    }
    result = await pipeline.run(snapshot, spec.payload, user_id=user.id)
    assert fake.invocation_count == 0
    assert {c.field_name for c in result.candidates} == {"公司名", "官网"}


class _TimeoutThenSuccessAgent:
    """首次超时、缩小上下文后成功；记录每次发送的正文长度。"""

    def __init__(self, timeout_error: Exception) -> None:
        self._timeout = timeout_error
        self.calls: list[int] = []

    async def extract(self, inp, resolved=None, api_key=None):
        self.calls.append(len(inp.readable_text))
        if len(self.calls) == 1:
            raise self._timeout
        return SemanticExtractionResult(
            fields=[
                SemanticFieldCandidate(
                    field_name="主营产品",
                    value="工业自动化设备与工业机器人",
                    evidence_quote="主营工业自动化设备与工业机器人",
                    confidence=0.8,
                )
            ]
        )


@pytest.mark.asyncio
async def test_llm_timeout_retries_with_reduced_context(ctx, storage):
    from app.providers.errors import ProviderTimeoutError, TimeoutPhase

    db = ctx["db"]
    user = ctx["user"]
    # 正文足够长，让 llm_max_context_chars 截断生效（默认 12_000）
    long_text = "主营工业自动化设备与工业机器人。" * 2000
    long_body = f"<html><body><p>{long_text}</p></body></html>".encode()
    snap_id = seed_snapshot(ctx, long_body, storage)
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(user.id, ctx["task"].id, 1)
    fake = _TimeoutThenSuccessAgent(ProviderTimeoutError(phase=TimeoutPhase.OVERALL))
    pipeline = ExtractionPipeline(
        db, storage, context_builder=ExtractionContextBuilder(db, storage), llm_agent=fake
    )
    result = await pipeline.run(snapshot, spec.payload, user_id=user.id)
    assert len(fake.calls) == 2
    # D-013：重试缩小了上下文（改变了输入），不是同样 prompt 再试一次
    assert fake.calls[1] < fake.calls[0]
    assert fake.calls[1] <= pipeline._settings.llm_retry_reduced_context_chars
    assert result.technical_metadata["llm_retries"] == 1
    assert result.technical_metadata["llm_invocations"] == 2


@pytest.mark.asyncio
async def test_llm_timeout_retry_still_times_out_is_bounded(ctx, storage):
    from app.providers.errors import ProviderTimeoutError, TimeoutPhase

    db = ctx["db"]
    user = ctx["user"]
    snap_id = seed_snapshot(ctx, HTML.encode("utf-8"), storage)
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(user.id, ctx["task"].id, 1)

    class _AlwaysTimeout:
        def __init__(self) -> None:
            self.calls = 0

        async def extract(self, inp, resolved=None, api_key=None):
            self.calls += 1
            raise ProviderTimeoutError(phase=TimeoutPhase.OVERALL)

    fake = _AlwaysTimeout()
    pipeline = ExtractionPipeline(
        db, storage, context_builder=ExtractionContextBuilder(db, storage), llm_agent=fake
    )
    with pytest.raises(ProviderTimeoutError):
        await pipeline.run(snapshot, spec.payload, user_id=user.id)
    assert fake.calls == 2  # 初始 + 1 次缩小重试，绝不无限重试


@pytest.mark.asyncio
async def test_llm_non_timeout_error_is_not_retried(ctx, storage):
    from app.providers.errors import ProviderInferenceError

    db = ctx["db"]
    user = ctx["user"]
    snap_id = seed_snapshot(ctx, HTML.encode("utf-8"), storage)
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(user.id, ctx["task"].id, 1)

    class _FailsOnce:
        def __init__(self) -> None:
            self.calls = 0

        async def extract(self, inp, resolved=None, api_key=None):
            self.calls += 1
            raise ProviderInferenceError("模型未返回可用内容")

    fake = _FailsOnce()
    pipeline = ExtractionPipeline(
        db, storage, context_builder=ExtractionContextBuilder(db, storage), llm_agent=fake
    )
    with pytest.raises(ProviderInferenceError):
        await pipeline.run(snapshot, spec.payload, user_id=user.id)
    assert fake.calls == 1  # 非超时失败绝不重试


@pytest.mark.asyncio
async def test_llm_context_is_bounded_to_llm_max_context_chars(ctx, storage):
    from app.extraction.contracts import ExtractionSettings

    db = ctx["db"]
    user = ctx["user"]
    long_body = ("<html><body><p>" + "长" * 40000 + "</p></body></html>").encode()
    snap_id = seed_snapshot(ctx, long_body, storage)
    snapshot = db.get(PageSnapshot, snap_id)
    spec = SpecVersionRepository(db).get_version(user.id, ctx["task"].id, 1)

    class _CaptureContext:
        def __init__(self) -> None:
            self.sent_len = 0

        async def extract(self, inp, resolved=None, api_key=None):
            self.sent_len = len(inp.readable_text)
            return SemanticExtractionResult(
                fields=[
                    SemanticFieldCandidate(
                        field_name="主营产品",
                        value="工业自动化设备",
                        evidence_quote="工业自动化设备",
                        confidence=0.8,
                    )
                ]
            )

    fake = _CaptureContext()
    settings = ExtractionSettings()
    pipeline = ExtractionPipeline(
        db,
        storage,
        context_builder=ExtractionContextBuilder(db, storage),
        llm_agent=fake,
        settings=settings,
    )
    await pipeline.run(snapshot, spec.payload, user_id=user.id)
    # LLM fallback 只接收 llm_max_context_chars 内的正文，而非 max_context_chars 的 30_000
    assert fake.sent_len == settings.llm_max_context_chars
