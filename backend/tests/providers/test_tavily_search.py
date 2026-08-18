"""Tavily search provider contract（DEPLOY-GATE-3 最小兼容）：POST /search + content 字段映射。"""

from __future__ import annotations

import pytest
from app.providers.protocol import ProviderTestStatus
from app.providers.registry import build_search_provider, list_search_provider_definitions
from app.providers.search_protocol import SearchResult
from tests.providers.fake_transport import FakeHttpClient


def test_tavily_registered_in_search_definitions() -> None:
    types = {d.provider_type for d in list_search_provider_definitions()}
    assert "tavily" in types


@pytest.mark.asyncio
async def test_tavily_connection_available() -> None:
    fake = FakeHttpClient(status_code=200, body={"results": []})
    provider = build_search_provider("tavily", http=fake)
    result = await provider.test_connection(api_key="tvly-test", base_url="https://api.tavily.com")
    assert result.status is ProviderTestStatus.AVAILABLE
    # 必须 POST 到 {base_url}/search，带 Bearer + JSON body
    call = fake.calls[-1]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.tavily.com/search"
    assert call["headers"]["Authorization"] == "Bearer tvly-test"
    assert call["body"]["search_depth"] == "basic"
    assert call["body"]["max_results"] == 1


@pytest.mark.asyncio
async def test_tavily_search_maps_content_to_snippet() -> None:
    fake = FakeHttpClient(
        status_code=200,
        body={
            "results": [
                {
                    "title": "Tavily",
                    "url": "https://tavily.com",
                    "content": "AI search API",
                    "score": 0.9,
                },
                {
                    "title": "Second",
                    "url": "https://b.example",
                    "content": "B snippet",
                    "score": 0.8,
                },
            ]
        },
    )
    provider = build_search_provider("tavily", http=fake)
    results = await provider.search(
        query="kairos", limit=5, api_key="tvly-test", base_url="https://api.tavily.com"
    )
    assert results[0] == SearchResult(
        url="https://tavily.com",
        title="Tavily",
        snippet="AI search API",
        provider="tavily",
        rank=1,
        query="kairos",
    )
    assert results[1].rank == 2  # rank 按 results 顺序产生


@pytest.mark.asyncio
async def test_tavily_search_caps_max_results_and_default_base_url() -> None:
    fake = FakeHttpClient(status_code=200, body={"results": []})
    provider = build_search_provider("tavily", http=fake)
    await provider.search(query="x", limit=50, api_key="tvly-test", base_url=None)
    call = fake.calls[-1]
    assert call["url"] == "https://api.tavily.com/search"  # 无 base_url 用官方默认
    assert call["body"]["max_results"] == 5  # 上限 5（Gate-3 小查询）


@pytest.mark.asyncio
async def test_tavily_auth_failure_maps_to_auth_failed() -> None:
    provider = build_search_provider("tavily", http=FakeHttpClient(status_code=401, body={}))
    result = await provider.test_connection(api_key="bad-key", base_url="https://api.tavily.com")
    assert result.status is ProviderTestStatus.AUTH_FAILED


@pytest.mark.asyncio
async def test_tavily_search_raises_on_rate_limited() -> None:
    # search() 不得把 429 静默吞成 0 结果（M-16 retry 层依赖异常分类）
    from app.providers import errors

    provider = build_search_provider("tavily", http=FakeHttpClient(status_code=429, body={}))
    with pytest.raises(errors.ProviderRateLimitedError):
        await provider.search(query="x", limit=5, api_key="k", base_url=None)
