"""ModelInferenceClient wire formats + error taxonomy (M-06, no network)."""

from __future__ import annotations

import asyncio

import pytest
from app.config import Settings
from app.providers import errors
from app.providers.inference import ModelInferenceClient, _map_http_error
from app.providers.inference_policy import InferenceIntent
from app.providers.protocol import ResolvedModel
from app.providers.transport import HttpResponse
from tests.providers.fake_transport import FakeHttpClient

SYSTEM = "你是目标理解模块"
USER = "帮我搜集供应商"


class SequencedHttpClient:
    def __init__(self, effects: list[Exception | HttpResponse]) -> None:
        self.effects = list(effects)
        self.calls: list[dict] = []

    async def request(self, **kwargs) -> HttpResponse:
        self.calls.append(kwargs)
        effect = self.effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class SlowHttpClient:
    def __init__(self) -> None:
        self.calls = 0

    async def request(self, **kwargs) -> HttpResponse:
        self.calls += 1
        await asyncio.sleep(0.05)
        return _success_response()


def _success_response() -> HttpResponse:
    return HttpResponse(200, {"choices": [{"message": {"content": '{"ok":true}'}}]})


def _bounded_client(http, *, timeout_seconds: float = 1.0) -> ModelInferenceClient:
    settings = Settings(
        provider_inference_timeout_seconds=timeout_seconds,
        capacity_default_retry_max_attempts=3,
        provider_throttle_min_interval_seconds=0,
        provider_throttle_max_burst=100,
    )
    return ModelInferenceClient(
        intent=InferenceIntent.PLAN_STRUCTURED,
        settings=settings,
        http=http,
        timeout_seconds=timeout_seconds,
        retry_base_delay_seconds=0,
        retry_rand=lambda: 0,
    )


def _deepseek(credential_version_id: int) -> ResolvedModel:
    return ResolvedModel(
        "deepseek",
        "deepseek-chat",
        "https://api.deepseek.com/v1",
        credential_version_id,
    )


async def _run(fake, resolved, api_key="sk-test") -> str:
    client = ModelInferenceClient(
        intent=InferenceIntent.GOAL_EXTRACTION,
        http=fake,
        retry_base_delay_seconds=0.001,
    )
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
        (400, errors.ProviderInferenceError),
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


@pytest.mark.asyncio
async def test_connect_timeout_retries_once_then_succeeds() -> None:
    """Removing the one connect retry must fail by returning no successful result."""
    fake = SequencedHttpClient(
        [
            errors.ProviderTimeoutError(phase=errors.TimeoutPhase.CONNECT),
            _success_response(),
        ]
    )

    result = await _bounded_client(fake).generate(
        resolved=_deepseek(9101), api_key="test", system=SYSTEM, user=USER
    )

    assert result.text == '{"ok":true}'
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_read_timeout_is_not_retried() -> None:
    """Retrying a read timeout can duplicate a charged model generation."""
    fake = SequencedHttpClient([errors.ProviderTimeoutError(phase=errors.TimeoutPhase.READ)])

    with pytest.raises(errors.ProviderTimeoutError) as caught:
        await _bounded_client(fake).generate(
            resolved=_deepseek(9102), api_key="test", system=SYSTEM, user=USER
        )

    assert caught.value.phase is errors.TimeoutPhase.READ
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_logical_deadline_covers_complete_provider_operation() -> None:
    """A stalled transport must be bounded by the logical operation deadline."""
    fake = SlowHttpClient()

    with pytest.raises(errors.ProviderTimeoutError) as caught:
        await _bounded_client(fake, timeout_seconds=0.01).generate(
            resolved=_deepseek(9103), api_key="test", system=SYSTEM, user=USER
        )

    assert caught.value.phase is errors.TimeoutPhase.OVERALL
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_connect_error_keeps_existing_bounded_network_retries() -> None:
    """Removing the existing network budget or making it unbounded must fail."""
    fake = SequencedHttpClient([errors.ProviderNetworkError("down") for _ in range(3)])

    with pytest.raises(errors.ProviderNetworkError):
        await _bounded_client(fake).generate(
            resolved=_deepseek(9104), api_key="test", system=SYSTEM, user=USER
        )

    assert len(fake.calls) == 3


@pytest.mark.asyncio
async def test_unexpected_type_error_is_not_wrapped_or_retried() -> None:
    """The old catch-all incorrectly reports programming bugs as provider failures."""
    fake = SequencedHttpClient([TypeError("fixture programming bug")])

    with pytest.raises(TypeError, match="fixture programming bug"):
        await _bounded_client(fake).generate(
            resolved=_deepseek(9105), api_key="test", system=SYSTEM, user=USER
        )

    assert len(fake.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, errors.ProviderAuthFailedError),
        (403, errors.ProviderAuthFailedError),
        (404, errors.ProviderModelNotFoundError),
    ],
)
async def test_auth_and_model_http_errors_are_not_retried(
    status: int, expected: type[errors.ProviderError]
) -> None:
    fake = SequencedHttpClient([HttpResponse(status, {})])

    with pytest.raises(expected):
        await _bounded_client(fake).generate(
            resolved=_deepseek(9200 + status), api_key="test", system=SYSTEM, user=USER
        )

    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_rate_limit_uses_safe_retry_after_metadata() -> None:
    """Dropping Retry-After must change the retry metadata and fail this test."""
    fake = SequencedHttpClient(
        [
            HttpResponse(
                429,
                {},
                headers={"retry-after": "0.01", "x-request-id": "req-rate-1"},
            ),
            _success_response(),
        ]
    )

    result = await _bounded_client(fake).generate(
        resolved=_deepseek(9106), api_key="test", system=SYSTEM, user=USER
    )

    assert result.text == '{"ok":true}'
    assert len(fake.calls) == 2


def test_rate_limit_error_retains_only_retry_metadata() -> None:
    mapped = _map_http_error(
        HttpResponse(
            429,
            {},
            headers={"retry-after": "0.01", "x-request-id": "req-rate-2"},
        )
    )

    assert isinstance(mapped, errors.ProviderRateLimitedError)
    assert mapped.retry_after_seconds == 0.01
    assert mapped.request_id == "req-rate-2"
