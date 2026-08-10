"""Provider connection-test error mapping via fake transport."""

from __future__ import annotations

import pytest
from app.providers.protocol import ProviderTestStatus
from app.providers.registry import build_model_provider
from tests.providers.fake_transport import FakeHttpClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, ProviderTestStatus.AVAILABLE),
        (401, ProviderTestStatus.AUTH_FAILED),
        (403, ProviderTestStatus.AUTH_FAILED),
        (404, ProviderTestStatus.MODEL_NOT_FOUND),
        (429, ProviderTestStatus.RATE_LIMITED),
    ],
)
async def test_openai_compatible_status_mapping(status: int, expected: ProviderTestStatus) -> None:
    provider = build_model_provider("openai", http=FakeHttpClient(status_code=status, body={}))
    result = await provider.test_connection(api_key="sk-test", model="gpt-4o-mini", base_url=None)
    assert result.status is expected


@pytest.mark.asyncio
async def test_network_error_maps_to_network_error() -> None:
    provider = build_model_provider("anthropic", http=FakeHttpClient(raise_network=True))
    result = await provider.test_connection(
        api_key="sk-test", model="claude-3-5-sonnet", base_url=None
    )
    assert result.status is ProviderTestStatus.NETWORK_ERROR


@pytest.mark.asyncio
async def test_gemini_400_maps_to_auth_failed() -> None:
    provider = build_model_provider("gemini", http=FakeHttpClient(status_code=400, body={}))
    result = await provider.test_connection(api_key="bad", model="gemini-1.5-pro", base_url=None)
    assert result.status is ProviderTestStatus.AUTH_FAILED
