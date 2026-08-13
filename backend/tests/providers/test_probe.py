"""Model probe behavior (SQLite + fake transport; no real network or keys)."""

from __future__ import annotations

import pytest
from app.credentials.models import Credential, CredentialVersion, ModelConfig
from app.providers.protocol import DetectionConfidence, ProviderTestStatus
from tests.providers.fake_transport import FakeHttpClient


async def _run(probe_factory, http, **kwargs):
    service, db, user = probe_factory(http)
    try:
        result = await service.probe_model(**kwargs)
        return service, db, user, result
    finally:
        db.close()


@pytest.mark.asyncio
async def test_probe_fingerprint_success(probe_factory) -> None:
    http = FakeHttpClient(200, {"data": []})
    service, db, user, result = await _run(
        probe_factory,
        http,
        api_key="sk-ant-abcdef",
        provider_type=None,
        base_url=None,
        model_name=None,
    )
    assert result.status is ProviderTestStatus.AVAILABLE
    assert result.detected_provider == "anthropic"
    assert result.detection_confidence is DetectionConfidence.HIGH
    assert result.probe_method == "fingerprint"
    assert result.resolved_base_url == "https://api.anthropic.com"
    assert result.latency_ms is not None
    assert result.message == "连接成功"
    # Exactly one request, to the detected provider only.
    assert len(http.calls) == 1
    assert "api.anthropic.com" in http.calls[0]["url"]
    # No persistence.
    assert service.list_model_configs(user) == []
    assert db.query(ModelConfig).count() == 0
    assert db.query(Credential).count() == 0
    assert db.query(CredentialVersion).count() == 0
    # Key never surfaces in the result.
    assert "sk-ant-abcdef" not in str(result)


@pytest.mark.asyncio
async def test_probe_auth_failed(probe_factory) -> None:
    http = FakeHttpClient(401)
    _, _, _, result = await _run(
        probe_factory,
        http,
        api_key="sk-ant-abcdef",
        provider_type=None,
        base_url=None,
        model_name=None,
    )
    assert result.status is ProviderTestStatus.AUTH_FAILED
    assert result.error_code == "HTTP_401"
    assert result.message == "API Key 无效"
    assert len(http.calls) == 1
    assert "sk-ant-abcdef" not in str(result)


@pytest.mark.asyncio
async def test_probe_network_error(probe_factory) -> None:
    http = FakeHttpClient(200, raise_network=True)
    _, _, _, result = await _run(
        probe_factory,
        http,
        api_key="sk-ant-abcdef",
        provider_type=None,
        base_url=None,
        model_name=None,
    )
    assert result.status is ProviderTestStatus.NETWORK_ERROR
    assert result.error_code == "NETWORK_ERROR"
    assert result.message == "无法连接服务商"


@pytest.mark.asyncio
async def test_probe_ambiguous_never_calls_adapter(probe_factory) -> None:
    http = FakeHttpClient(200, {"data": []})
    _, _, _, result = await _run(
        probe_factory,
        http,
        api_key="sk-1234567890abcdef",
        provider_type=None,
        base_url=None,
        model_name=None,
    )
    assert result.status is None
    assert result.detection_confidence is DetectionConfidence.AMBIGUOUS
    assert result.detected_provider is None
    assert set(result.candidates) == {"openai", "deepseek"}
    assert "请选择 Provider" in result.message
    # The key was never sent anywhere.
    assert http.calls == []


@pytest.mark.asyncio
async def test_probe_none_requires_manual(probe_factory) -> None:
    http = FakeHttpClient(200, {"data": []})
    _, _, _, result = await _run(
        probe_factory,
        http,
        api_key="opaque-token",
        provider_type=None,
        base_url=None,
        model_name=None,
    )
    assert result.status is None
    assert result.detection_confidence is DetectionConfidence.NONE
    assert http.calls == []


@pytest.mark.asyncio
async def test_probe_manual_selection(probe_factory) -> None:
    http = FakeHttpClient(200, {"data": []})
    _, _, _, result = await _run(
        probe_factory,
        http,
        api_key="sk-whatever",
        provider_type="deepseek",
        base_url=None,
        model_name=None,
    )
    assert result.status is ProviderTestStatus.AVAILABLE
    assert result.detected_provider == "deepseek"
    assert result.probe_method == "manual"
    assert result.resolved_base_url == "https://api.deepseek.com/v1"
    assert len(http.calls) == 1
    assert "api.deepseek.com" in http.calls[0]["url"]


@pytest.mark.asyncio
async def test_probe_custom_requires_base_url(probe_factory) -> None:
    http = FakeHttpClient(200, {"data": []})
    _, _, _, result = await _run(
        probe_factory,
        http,
        api_key="sk-whatever",
        provider_type="custom_openai_compatible",
        base_url=None,
        model_name=None,
    )
    assert result.status is None
    assert result.error_code == "BASE_URL_REQUIRED"
    assert http.calls == []


@pytest.mark.asyncio
async def test_probe_invalid_base_url(probe_factory) -> None:
    http = FakeHttpClient(200, {"data": []})
    _, _, _, result = await _run(
        probe_factory,
        http,
        api_key="sk-whatever",
        provider_type="custom_openai_compatible",
        base_url="not-a-valid-url",
        model_name=None,
    )
    assert result.status is None
    assert result.error_code == "INVALID_BASE_URL"
    assert http.calls == []


@pytest.mark.asyncio
async def test_probe_ollama_requires_no_key(probe_factory) -> None:
    http = FakeHttpClient(200, {"models": []})
    _, _, _, result = await _run(
        probe_factory,
        http,
        api_key=None,
        provider_type="ollama",
        base_url="http://localhost:11434",
        model_name=None,
    )
    assert result.status is ProviderTestStatus.AVAILABLE
    assert result.detected_provider == "ollama"
    assert result.latency_ms is not None
    assert len(http.calls) == 1
