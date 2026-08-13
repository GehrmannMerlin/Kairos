"""Search provider probe + per-definition create/update validation (SQLite,
fake transport — no real network or keys).

Mirrors the model probe security contract: the unsaved API key is used in a
single real request, is never persisted, never logged, and never echoed.
"""

from __future__ import annotations

import pytest
from app.credentials.models import Credential, CredentialVersion, SearchConfig
from app.providers import errors as perr
from app.providers.protocol import ProviderTestStatus
from tests.providers.fake_transport import FakeHttpClient

TAVILY = "https://api.tavily.com"


async def _probe(probe_factory, http, **kwargs):
    service, db, user = probe_factory(http)
    try:
        result = await service.probe_search(**kwargs)
        return service, db, user, result
    finally:
        db.close()


# ---- Probe: status mapping + latency ----


@pytest.mark.asyncio
async def test_probe_tavily_available(probe_factory) -> None:
    http = FakeHttpClient(200, {"results": []})
    service, db, user, result = await _probe(
        probe_factory, http, provider_type="tavily", api_key="tvly-secret", base_url=None
    )
    assert result.status is ProviderTestStatus.AVAILABLE
    assert result.provider_type == "tavily"
    assert result.resolved_base_url == TAVILY
    assert result.latency_ms is not None and result.latency_ms >= 0
    # Exactly one request, to the managed Tavily endpoint only.
    assert len(http.calls) == 1
    assert http.calls[0]["url"] == f"{TAVILY}/search"
    assert http.calls[0]["headers"]["Authorization"] == "Bearer tvly-secret"
    # No persistence whatsoever.
    assert service.list_search_configs(user) == []
    assert db.query(SearchConfig).count() == 0
    assert db.query(Credential).count() == 0
    assert db.query(CredentialVersion).count() == 0
    # Key never surfaces in the result.
    assert "tvly-secret" not in str(result)


@pytest.mark.asyncio
async def test_probe_tavily_auth_failed(probe_factory) -> None:
    http = FakeHttpClient(401)
    _, _, _, result = await _probe(
        probe_factory, http, provider_type="tavily", api_key="tvly-bad", base_url=None
    )
    assert result.status is ProviderTestStatus.AUTH_FAILED
    assert result.error_code == "HTTP_401"
    assert result.message == "API Key 无效"
    assert "tvly-bad" not in str(result)


@pytest.mark.asyncio
async def test_probe_tavily_rate_limited(probe_factory) -> None:
    http = FakeHttpClient(429)
    _, _, _, result = await _probe(
        probe_factory, http, provider_type="tavily", api_key="tvly-x", base_url=None
    )
    assert result.status is ProviderTestStatus.RATE_LIMITED
    assert result.message == "服务商返回限流，请稍后重试"


@pytest.mark.asyncio
async def test_probe_tavily_network_error(probe_factory) -> None:
    http = FakeHttpClient(200, raise_network=True)
    _, _, _, result = await _probe(
        probe_factory, http, provider_type="tavily", api_key="tvly-x", base_url=None
    )
    assert result.status is ProviderTestStatus.NETWORK_ERROR
    assert result.message == "无法连接服务商"


@pytest.mark.asyncio
async def test_probe_tavily_failed(probe_factory) -> None:
    http = FakeHttpClient(500)
    _, _, _, result = await _probe(
        probe_factory, http, provider_type="tavily", api_key="tvly-x", base_url=None
    )
    assert result.status is ProviderTestStatus.FAILED
    assert result.error_code == "HTTP_500"


# ---- Probe: per-definition field requirements ----


@pytest.mark.asyncio
async def test_probe_custom_requires_base_url(probe_factory) -> None:
    http = FakeHttpClient(200, {"results": []})
    _, _, _, result = await _probe(
        probe_factory,
        http,
        provider_type="custom_compatible_search",
        api_key="sk-x",
        base_url=None,
    )
    assert result.status is None
    assert result.error_code == "BASE_URL_REQUIRED"
    assert http.calls == []


@pytest.mark.asyncio
async def test_probe_custom_invalid_base_url(probe_factory) -> None:
    http = FakeHttpClient(200, {"results": []})
    _, _, _, result = await _probe(
        probe_factory,
        http,
        provider_type="custom_compatible_search",
        api_key="sk-x",
        base_url="not-a-valid-url",
    )
    assert result.status is None
    assert result.error_code == "INVALID_BASE_URL"
    assert http.calls == []


@pytest.mark.asyncio
async def test_probe_tavily_requires_api_key(probe_factory) -> None:
    http = FakeHttpClient(200, {"results": []})
    _, _, _, result = await _probe(
        probe_factory, http, provider_type="tavily", api_key=None, base_url=None
    )
    assert result.status is None
    assert result.error_code == "API_KEY_REQUIRED"
    assert http.calls == []


@pytest.mark.asyncio
async def test_probe_custom_available_with_user_base_url(probe_factory) -> None:
    http = FakeHttpClient(200, {"results": []})
    _, _, _, result = await _probe(
        probe_factory,
        http,
        provider_type="custom_compatible_search",
        api_key="sk-x",
        base_url="http://127.0.0.1:9999",
    )
    assert result.status is ProviderTestStatus.AVAILABLE
    assert result.resolved_base_url == "http://127.0.0.1:9999"
    assert len(http.calls) == 1
    assert http.calls[0]["url"] == "http://127.0.0.1:9999/search"


@pytest.mark.asyncio
async def test_probe_invalid_provider_type_rejected(probe_factory) -> None:
    with pytest.raises(perr.ProviderValidationError):
        await _probe(
            probe_factory, FakeHttpClient(200), provider_type="nope", api_key="x", base_url=None
        )


# ---- Create/update validation driven by the registry definition ----


def test_create_tavily_without_base_url_ok(service_and_db) -> None:
    service, db = service_and_db
    from app.auth.repository import UserRepository

    user = UserRepository(db).create("a@example.com", "hash", None)
    config = service.create_search_config(
        user, name="Tavily", provider_type="tavily", base_url=None, api_key="tvly-secret"
    )
    assert config.provider_type == "tavily"
    assert config.base_url is None


def test_create_custom_without_base_url_rejected(service_and_db) -> None:
    service, db = service_and_db
    from app.auth.repository import UserRepository

    user = UserRepository(db).create("a@example.com", "hash", None)
    with pytest.raises(perr.ProviderValidationError):
        service.create_search_config(
            user,
            name="custom",
            provider_type="custom_compatible_search",
            base_url=None,
            api_key="sk-x",
        )


def test_create_custom_with_invalid_base_url_rejected(service_and_db) -> None:
    service, db = service_and_db
    from app.auth.repository import UserRepository

    user = UserRepository(db).create("a@example.com", "hash", None)
    with pytest.raises(perr.ProviderValidationError):
        service.create_search_config(
            user,
            name="custom",
            provider_type="custom_compatible_search",
            base_url="ftp://bad",
            api_key="sk-x",
        )


def test_update_custom_to_empty_base_url_rejected(service_and_db) -> None:
    service, db = service_and_db
    from app.auth.repository import UserRepository

    user = UserRepository(db).create("a@example.com", "hash", None)
    created = service.create_search_config(
        user,
        name="custom",
        provider_type="custom_compatible_search",
        base_url="http://search:9000",
        api_key="sk-x",
    )
    with pytest.raises(perr.ProviderValidationError):
        service.update_search_config(
            user,
            config_id=created.config_id,
            name="custom",
            provider_type="custom_compatible_search",
            base_url=None,
        )
