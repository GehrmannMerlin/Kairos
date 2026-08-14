"""Provider-supplied model catalog parsing (no external network or keys)."""

from __future__ import annotations

import pytest
from app.providers.protocol import ProviderTestStatus
from app.providers.registry import build_model_provider
from tests.providers.fake_transport import FakeHttpClient


@pytest.mark.asyncio
async def test_openai_compatible_catalog_uses_provider_ids_in_provider_order() -> None:
    fake = FakeHttpClient(
        200,
        {
            "object": "list",
            "data": [
                {"id": "deepseek-v4-flash", "object": "model", "owned_by": "deepseek"},
                {"id": "deepseek-v4-pro", "object": "model", "owned_by": "deepseek"},
                {"id": "deepseek-v4-flash", "object": "model", "owned_by": "deepseek"},
            ],
        },
    )

    result = await build_model_provider("deepseek", http=fake).list_models(
        api_key="fixture-key", base_url=None
    )

    assert result.status is ProviderTestStatus.AVAILABLE
    assert result.models == ("deepseek-v4-flash", "deepseek-v4-pro")
    assert fake.calls[0]["url"] == "https://api.deepseek.com/v1/models"


@pytest.mark.asyncio
async def test_anthropic_catalog_uses_data_ids() -> None:
    fake = FakeHttpClient(
        200,
        {
            "data": [
                {
                    "type": "model",
                    "id": "claude-opus-4-1",
                    "display_name": "Claude Opus 4.1",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
            "has_more": False,
            "first_id": "claude-opus-4-1",
            "last_id": "claude-opus-4-1",
        },
    )

    result = await build_model_provider("anthropic", http=fake).list_models(
        api_key="fixture-key", base_url=None
    )

    assert result.status is ProviderTestStatus.AVAILABLE
    assert result.models == ("claude-opus-4-1",)
    assert fake.calls[0]["headers"]["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_gemini_catalog_keeps_only_generate_content_models() -> None:
    fake = FakeHttpClient(
        200,
        {
            "models": [
                {
                    "name": "models/gemini-3.6-flash-001",
                    "baseModelId": "gemini-3.6-flash",
                    "version": "001",
                    "displayName": "Gemini 3.6 Flash",
                    "description": "generation model",
                    "inputTokenLimit": 1000000,
                    "outputTokenLimit": 65536,
                    "supportedGenerationMethods": ["generateContent", "countTokens"],
                },
                {
                    "name": "models/gemini-embedding-2",
                    "baseModelId": "gemini-embedding-2",
                    "version": "002",
                    "displayName": "Gemini Embedding 2",
                    "description": "embedding model",
                    "inputTokenLimit": 8192,
                    "outputTokenLimit": 1,
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        },
    )

    result = await build_model_provider("gemini", http=fake).list_models(
        api_key="fixture-key", base_url=None
    )

    assert result.status is ProviderTestStatus.AVAILABLE
    assert result.models == ("gemini-3.6-flash",)


@pytest.mark.asyncio
async def test_ollama_catalog_uses_locally_installed_model_ids() -> None:
    fake = FakeHttpClient(
        200,
        {
            "models": [
                {
                    "name": "gemma3:latest",
                    "model": "gemma3:latest",
                    "modified_at": "2026-01-01T00:00:00Z",
                    "size": 3338801804,
                    "digest": "fixture-digest",
                    "details": {"format": "gguf", "family": "gemma"},
                }
            ]
        },
    )

    result = await build_model_provider("ollama", http=fake).list_models(
        api_key=None, base_url="http://ollama.internal:11434"
    )

    assert result.status is ProviderTestStatus.AVAILABLE
    assert result.models == ("gemma3:latest",)
    assert fake.calls[0]["url"] == "http://ollama.internal:11434/api/tags"


@pytest.mark.asyncio
async def test_catalog_rejects_malformed_success_instead_of_returning_fake_options() -> None:
    result = await build_model_provider(
        "deepseek", http=FakeHttpClient(200, {"object": "list", "data": [{}]})
    ).list_models(api_key="fixture-key", base_url=None)

    assert result.status is ProviderTestStatus.FAILED
    assert result.models == ()


@pytest.mark.asyncio
async def test_catalog_maps_auth_rate_limit_and_network_without_raw_body() -> None:
    auth = await build_model_provider(
        "deepseek",
        http=FakeHttpClient(401, {"error": {"message": "fixture secret provider detail"}}),
    ).list_models(api_key="fixture-key", base_url=None)
    limited = await build_model_provider(
        "deepseek", http=FakeHttpClient(429, {"error": {"message": "provider detail"}})
    ).list_models(api_key="fixture-key", base_url=None)
    network = await build_model_provider(
        "deepseek", http=FakeHttpClient(raise_network=True)
    ).list_models(api_key="fixture-key", base_url=None)

    assert auth.status is ProviderTestStatus.AUTH_FAILED
    assert limited.status is ProviderTestStatus.RATE_LIMITED
    assert network.status is ProviderTestStatus.NETWORK_ERROR
    assert "fixture secret provider detail" not in repr(auth)
