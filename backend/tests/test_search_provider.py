from __future__ import annotations

import httpx
import pytest
from app.search import (
    NormalizedSearchResult,
    SearchProviderError,
    TavilySearchProvider,
    resolve_search_provider,
)


def _provider_for(handler) -> TavilySearchProvider:
    transport = httpx.MockTransport(handler)

    def client_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, base_url="https://api.tavily.com")

    return TavilySearchProvider(api_key="test-secret", client_factory=client_factory)


@pytest.mark.asyncio
async def test_tavily_results_are_normalized_capped_and_bounded() -> None:
    long_snippet = "snippet " * 200

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.headers["content-type"] == "application/json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/one",
                        "title": "One",
                        "content": long_snippet,
                        "score": 0.91,
                        "published_date": "2026-08-01",
                    },
                    {"url": "https://example.com/two", "title": "Two", "content": "Two text"},
                    {"url": "https://example.com/three", "title": "Three", "content": "Three text"},
                ]
            },
        )

    response = await _provider_for(handler).search(query="python web framework", max_results=2)

    assert response.provider == "tavily"
    assert response.query == "python web framework"
    assert len(response.results) == 2
    assert response.results[0] == NormalizedSearchResult(
        url="https://example.com/one",
        title="One",
        snippet=long_snippet[:500],
        rank=1,
        provider_score=0.91,
        published_at="2026-08-01",
    )


@pytest.mark.asyncio
async def test_search_provider_error_mapping_is_safe_and_does_not_expose_credential() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "secret api key test-secret is invalid"})

    with pytest.raises(SearchProviderError) as raised:
        await _provider_for(handler).search(query="python", max_results=3)

    assert raised.value.code == "SEARCH_PROVIDER_AUTH_FAILED"
    assert "test-secret" not in str(raised.value)
    assert "test-secret" not in raised.value.message


def test_search_provider_dto_contains_no_credential_fields() -> None:
    result = NormalizedSearchResult(
        url="https://example.com",
        title="Example",
        snippet="Example",
        rank=1,
    )

    serialized = result.model_dump_json()

    assert "api_key" not in serialized
    assert "credential" not in serialized


def test_search_provider_resolver_requires_worker_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("KAIROS_SEARCH_PROVIDER", "tavily")

    with pytest.raises(SearchProviderError) as raised:
        resolve_search_provider()

    assert raised.value.code == "SEARCH_PROVIDER_CREDENTIAL_REQUIRED"
    assert "api_key" not in raised.value.message.lower()
