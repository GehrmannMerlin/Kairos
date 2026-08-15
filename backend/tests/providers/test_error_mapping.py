"""Provider connection-test error mapping via fake transport."""

from __future__ import annotations

import httpx
import pytest
from app.providers import errors
from app.providers.protocol import ProviderTestStatus
from app.providers.registry import build_model_provider
from app.providers.transport import HttpxTransport
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
    body = (
        {"object": "list", "data": [{"id": "gpt-4o-mini", "object": "model"}]}
        if status == 200
        else {}
    )
    provider = build_model_provider("openai", http=FakeHttpClient(status_code=status, body=body))
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
async def test_saved_connection_rejects_model_missing_from_successful_catalog() -> None:
    provider = build_model_provider(
        "deepseek",
        http=FakeHttpClient(
            status_code=200,
            body={
                "object": "list",
                "data": [
                    {"id": "deepseek-v4-flash", "object": "model", "owned_by": "deepseek"},
                    {"id": "deepseek-v4-pro", "object": "model", "owned_by": "deepseek"},
                ],
            },
        ),
    )

    result = await provider.test_connection(api_key="fixture-key", model="DeepSeek", base_url=None)

    assert result.status is ProviderTestStatus.MODEL_NOT_FOUND
    assert result.error_code == "MODEL_NOT_FOUND"


@pytest.mark.asyncio
async def test_gemini_400_maps_to_auth_failed() -> None:
    provider = build_model_provider("gemini", http=FakeHttpClient(status_code=400, body={}))
    result = await provider.test_connection(api_key="bad", model="gemini-1.5-pro", base_url=None)
    assert result.status is ProviderTestStatus.AUTH_FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_error", "expected_type", "expected_phase"),
    [
        (httpx.ConnectTimeout("connect timeout"), errors.ProviderTimeoutError, "connect"),
        (httpx.ReadTimeout("read timeout"), errors.ProviderTimeoutError, "read"),
        (httpx.ConnectError("connect failed"), errors.ProviderNetworkError, None),
    ],
)
async def test_httpx_transport_preserves_known_network_failure_type(
    raw_error: Exception,
    expected_type: type[Exception],
    expected_phase: str | None,
) -> None:
    """Collapsing raw timeout phases at the transport boundary is the incident bug."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if isinstance(raw_error, httpx.RequestError):
            raw_error.request = request
        raise raw_error

    transport = HttpxTransport(transport=httpx.MockTransport(_handler))

    with pytest.raises(expected_type) as caught:
        await transport.request(
            method="POST",
            url="https://provider.example/v1/chat/completions",
            headers=None,
            params=None,
            timeout_seconds=1.0,
            body={},
        )

    if expected_phase is not None:
        assert caught.value.phase.value == expected_phase


@pytest.mark.asyncio
async def test_httpx_transport_retains_only_safe_response_headers() -> None:
    """Leaking arbitrary response metadata must fail this boundary test."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            request=request,
            json={},
            headers={
                "Retry-After": "7",
                "X-Request-ID": "req-123",
                "X-Provider-Secret": "must-not-escape",
            },
        )

    transport = HttpxTransport(transport=httpx.MockTransport(_handler))
    response = await transport.request(
        method="POST",
        url="https://provider.example/v1/chat/completions",
        headers=None,
        params=None,
        timeout_seconds=1.0,
        body={},
    )

    assert response.headers == {"retry-after": "7", "x-request-id": "req-123"}
