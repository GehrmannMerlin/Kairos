"""Search provider contract: registry + compatible adapter + result DTO."""

from __future__ import annotations

import pytest
from app.providers import errors as perr
from app.providers.protocol import ProviderTestStatus
from app.providers.registry import (
    build_search_provider,
    list_search_provider_definitions,
    validate_search_provider_type,
)
from app.providers.search_protocol import SearchResult
from tests.providers.fake_transport import FakeHttpClient


def test_search_registry_has_compatible_provider() -> None:
    types = {d.provider_type for d in list_search_provider_definitions()}
    assert "custom_compatible_search" in types


def test_invalid_search_provider_rejected() -> None:
    with pytest.raises(perr.ProviderValidationError):
        validate_search_provider_type("not_a_real_provider")


@pytest.mark.asyncio
async def test_search_connection_available() -> None:
    fake = FakeHttpClient(status_code=200, body={"results": []})
    provider = build_search_provider("custom_compatible_search", http=fake)
    result = await provider.test_connection(api_key="sk-test", base_url="http://stub/search")
    assert result.status is ProviderTestStatus.AVAILABLE


@pytest.mark.asyncio
async def test_search_parses_results() -> None:
    fake = FakeHttpClient(
        status_code=200,
        body={"results": [{"url": "https://a.example", "title": "A", "snippet": "..."}]},
    )
    provider = build_search_provider("custom_compatible_search", http=fake)
    results = await provider.search(
        query="kairos", limit=5, api_key="sk-test", base_url="http://stub/search"
    )
    assert results[0] == SearchResult(
        url="https://a.example",
        title="A",
        snippet="...",
        provider="custom_compatible_search",
        rank=1,
        query="kairos",
    )


@pytest.mark.asyncio
async def test_search_429_maps_to_rate_limited() -> None:
    provider = build_search_provider(
        "custom_compatible_search", http=FakeHttpClient(status_code=429, body={})
    )
    result = await provider.test_connection(api_key="sk-test", base_url="http://stub/search")
    assert result.status is ProviderTestStatus.RATE_LIMITED


@pytest.mark.asyncio
async def test_search_404_maps_to_network_error() -> None:
    provider = build_search_provider(
        "custom_compatible_search", http=FakeHttpClient(status_code=404, body={})
    )
    result = await provider.test_connection(api_key="sk-test", base_url="http://stub/search")
    assert result.status is ProviderTestStatus.NETWORK_ERROR
