"""Bocha Web Search provider contract（M-03 / D-069）。"""

from __future__ import annotations

import pytest
from app.providers.protocol import ProviderTestStatus
from app.providers.registry import build_search_provider, list_search_provider_definitions
from app.providers.search_protocol import SearchResult
from tests.providers.fake_transport import FakeHttpClient


def test_bocha_registered_in_search_definitions() -> None:
    types = {d.provider_type for d in list_search_provider_definitions()}
    assert "bocha" in types


@pytest.mark.asyncio
async def test_bocha_connection_available() -> None:
    fake = FakeHttpClient(status_code=200, body={"data": {"webPages": {"value": []}}})
    provider = build_search_provider("bocha", http=fake)
    result = await provider.test_connection(api_key="sk-bocha", base_url="https://api.bochaai.com")
    assert result.status is ProviderTestStatus.AVAILABLE
    # 必须 POST 到 {base_url}/v1/web-search，带 Bearer + JSON body
    call = fake.calls[-1]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.bochaai.com/v1/web-search"
    assert call["headers"]["Authorization"] == "Bearer sk-bocha"
    assert call["body"]["query"] == "kairos"
    assert call["body"]["count"] == 1


@pytest.mark.asyncio
async def test_bocha_search_maps_webpages_value() -> None:
    fake = FakeHttpClient(
        status_code=200,
        body={
            "code": "0",
            "data": {
                "webPages": {
                    "value": [
                        {
                            "name": "Bocha",
                            "url": "https://www.bochaai.com",
                            "snippet": "AI search API",
                        },
                        {"name": "Second", "url": "https://b.example", "snippet": "B snippet"},
                    ]
                }
            },
        },
    )
    provider = build_search_provider("bocha", http=fake)
    results = await provider.search(
        query="kairos", limit=5, api_key="sk-bocha", base_url="https://api.bochaai.com"
    )
    assert results[0] == SearchResult(
        url="https://www.bochaai.com",
        title="Bocha",
        snippet="AI search API",
        provider="bocha",
        rank=1,
        query="kairos",
    )
    assert results[1].rank == 2


@pytest.mark.asyncio
async def test_bocha_search_supports_top_level_webpages_shape() -> None:
    fake = FakeHttpClient(
        status_code=200,
        body={"webPages": {"value": [{"url": "https://x.example", "name": "X"}]}},
    )
    provider = build_search_provider("bocha", http=fake)
    results = await provider.search(query="x", limit=5, api_key="sk-bocha", base_url=None)
    assert len(results) == 1
    assert results[0].url == "https://x.example"
    # 无 base_url 用官方默认端点
    assert fake.calls[-1]["url"] == "https://api.bochaai.com/v1/web-search"


@pytest.mark.asyncio
async def test_bocha_search_empty_webpages_value() -> None:
    fake = FakeHttpClient(status_code=200, body={"data": {"webPages": {"value": []}}})
    provider = build_search_provider("bocha", http=fake)
    results = await provider.search(query="x", limit=5, api_key="sk-bocha", base_url=None)
    assert results == []


@pytest.mark.asyncio
async def test_bocha_search_caps_count_at_50_and_default_base_url() -> None:
    fake = FakeHttpClient(status_code=200, body={"data": {"webPages": {"value": []}}})
    provider = build_search_provider("bocha", http=fake)
    await provider.search(query="x", limit=999, api_key="sk-bocha", base_url=None)
    assert fake.calls[-1]["body"]["count"] == 50  # 官方上限 50


@pytest.mark.asyncio
async def test_bocha_search_keeps_url_when_title_or_snippet_missing() -> None:
    fake = FakeHttpClient(
        status_code=200,
        body={"data": {"webPages": {"value": [{"url": "https://only-url.example"}]}}},
    )
    provider = build_search_provider("bocha", http=fake)
    results = await provider.search(query="x", limit=5, api_key="sk-bocha", base_url=None)
    assert len(results) == 1
    assert results[0].url == "https://only-url.example"
    assert results[0].title == ""
    assert results[0].snippet == ""


@pytest.mark.asyncio
async def test_bocha_auth_failure_maps_to_auth_failed() -> None:
    provider = build_search_provider("bocha", http=FakeHttpClient(status_code=401, body={}))
    result = await provider.test_connection(api_key="bad", base_url="https://api.bochaai.com")
    assert result.status is ProviderTestStatus.AUTH_FAILED


@pytest.mark.asyncio
async def test_bocha_rate_limited_maps_to_rate_limited() -> None:
    provider = build_search_provider("bocha", http=FakeHttpClient(status_code=429, body={}))
    result = await provider.test_connection(api_key="k", base_url=None)
    assert result.status is ProviderTestStatus.RATE_LIMITED


@pytest.mark.asyncio
async def test_bocha_network_error_maps_to_network_error() -> None:
    provider = build_search_provider("bocha", http=FakeHttpClient(raise_network=True))
    result = await provider.test_connection(api_key="k", base_url=None)
    assert result.status is ProviderTestStatus.NETWORK_ERROR


@pytest.mark.asyncio
async def test_bocha_malformed_json_maps_to_failed() -> None:
    # 200 但 body 不是 JSON 对象（transport 解析失败回退为原始字符串）→ FAILED
    provider = build_search_provider("bocha", http=FakeHttpClient(status_code=200, body="<html>"))
    result = await provider.test_connection(api_key="k", base_url=None)
    assert result.status is ProviderTestStatus.FAILED


@pytest.mark.asyncio
async def test_bocha_search_raises_on_rate_limited() -> None:
    # search() 不得把 429 静默吞成 0 结果（M-16 retry 层依赖异常分类）
    from app.providers import errors

    provider = build_search_provider("bocha", http=FakeHttpClient(status_code=429, body={}))
    with pytest.raises(errors.ProviderRateLimitedError):
        await provider.search(query="x", limit=5, api_key="k", base_url=None)


@pytest.mark.asyncio
async def test_bocha_search_raises_on_auth_failed() -> None:
    from app.providers import errors

    provider = build_search_provider("bocha", http=FakeHttpClient(status_code=401, body={}))
    with pytest.raises(errors.ProviderAuthFailedError):
        await provider.search(query="x", limit=5, api_key="k", base_url=None)


@pytest.mark.asyncio
async def test_bocha_search_raises_on_server_error() -> None:
    from app.providers import errors

    provider = build_search_provider("bocha", http=FakeHttpClient(status_code=503, body={}))
    with pytest.raises(errors.ProviderNetworkError):
        await provider.search(query="x", limit=5, api_key="k", base_url=None)
