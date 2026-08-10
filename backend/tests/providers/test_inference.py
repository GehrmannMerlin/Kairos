"""ModelInferenceClient wire formats + error taxonomy (M-06, no network)."""

from __future__ import annotations

import pytest
from app.providers import errors
from app.providers.inference import ModelInferenceClient
from app.providers.protocol import ResolvedModel
from tests.providers.fake_transport import FakeHttpClient

SYSTEM = "你是目标理解模块"
USER = "帮我搜集供应商"


async def _run(fake, resolved, api_key="sk-test") -> str:
    client = ModelInferenceClient(http=fake)
    result = await client.generate(resolved=resolved, api_key=api_key, system=SYSTEM, user=USER)
    return result.text


@pytest.mark.asyncio
async def test_openai_compatible_calls_chat_completions() -> None:
    fake = FakeHttpClient(
        200, {"choices": [{"message": {"content": '{"task_type":"EXPLORATORY"}'}}]}
    )
    text = await _run(
        fake, ResolvedModel("openai", "gpt-4o-mini", "https://api.openai.com/v1", None)
    )
    assert '"EXPLORATORY"' in text
    call = fake.calls[0]
    assert call["url"] == "https://api.openai.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    # 请求体必须真实发送（含 model / messages / json_object 输出格式）。
    assert call["body"]["model"] == "gpt-4o-mini"
    assert call["body"]["messages"][0]["role"] == "system"
    assert call["body"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_ollama_omits_auth_header() -> None:
    fake = FakeHttpClient(200, {"choices": [{"message": {"content": '{"a":1}'}}]})
    await _run(fake, ResolvedModel("ollama", "qwen2.5", "http://localhost:11434", None))
    assert "Authorization" not in (fake.calls[0]["headers"] or {})


@pytest.mark.asyncio
async def test_anthropic_wire_format() -> None:
    fake = FakeHttpClient(200, {"content": [{"type": "text", "text": '{"a":1}'}]})
    text = await _run(
        fake, ResolvedModel("anthropic", "claude-3-5-sonnet", "https://api.anthropic.com", None)
    )
    assert '{"a":1}' in text
    call = fake.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "sk-test"
    assert call["headers"]["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_gemini_wire_format() -> None:
    fake = FakeHttpClient(200, {"candidates": [{"content": {"parts": [{"text": '{"a":1}'}]}}]})
    text = await _run(
        fake,
        ResolvedModel(
            "gemini",
            "gemini-1.5-flash",
            "https://generativelanguage.googleapis.com/v1beta",
            None,
        ),
    )
    assert '{"a":1}' in text
    call = fake.calls[0]
    assert call["url"].endswith(":generateContent")
    assert call["params"] == {"key": "sk-test"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "exc_type"),
    [
        (401, errors.ProviderAuthFailedError),
        (403, errors.ProviderAuthFailedError),
        (404, errors.ProviderModelNotFoundError),
        (429, errors.ProviderRateLimitedError),
        (500, errors.ProviderNetworkError),
    ],
)
async def test_http_error_mapping(status, exc_type) -> None:
    fake = FakeHttpClient(status, {})
    with pytest.raises(exc_type):
        await _run(fake, ResolvedModel("openai", "gpt-4o-mini", "https://api.openai.com/v1", None))


@pytest.mark.asyncio
async def test_network_error_mapping() -> None:
    fake = FakeHttpClient(raise_network=True)
    with pytest.raises(errors.ProviderNetworkError):
        await _run(fake, ResolvedModel("openai", "gpt-4o-mini", "https://api.openai.com/v1", None))


@pytest.mark.asyncio
async def test_empty_output_raises_inference_error() -> None:
    fake = FakeHttpClient(200, {"choices": []})
    with pytest.raises(errors.ProviderInferenceError):
        await _run(fake, ResolvedModel("openai", "gpt-4o-mini", "https://api.openai.com/v1", None))


@pytest.mark.asyncio
async def test_unknown_family_rejected() -> None:
    fake = FakeHttpClient(200, {"choices": [{"message": {"content": "x"}}]})
    with pytest.raises(errors.ProviderInferenceError):
        await _run(fake, ResolvedModel("mystery", "m", "https://x", None))
