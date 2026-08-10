"""Model provider registry: all seven first-party providers registered."""

from __future__ import annotations

import pytest
from app.providers import errors as perr
from app.providers.registry import (
    build_model_provider,
    list_model_provider_definitions,
    validate_model_provider_type,
)
from tests.providers.fake_transport import FakeHttpClient

EXPECTED_MODEL_PROVIDERS = {
    "openai",
    "anthropic",
    "gemini",
    "deepseek",
    "openrouter",
    "ollama",
    "custom_openai_compatible",
}


def test_all_seven_model_providers_registered() -> None:
    types = {d.provider_type for d in list_model_provider_definitions()}
    assert types >= EXPECTED_MODEL_PROVIDERS


def test_openai_family_share_protocol_family() -> None:
    families = {d.provider_type: d.protocol_family for d in list_model_provider_definitions()}
    for t in ("openai", "deepseek", "openrouter", "custom_openai_compatible"):
        assert families[t] == "openai_compatible"
    assert families["anthropic"] == "anthropic"
    assert families["gemini"] == "gemini"
    assert families["ollama"] == "ollama"


def test_ollama_does_not_require_key() -> None:
    defn = next(d for d in list_model_provider_definitions() if d.provider_type == "ollama")
    assert defn.requires_api_key is False


def test_invalid_provider_type_rejected() -> None:
    with pytest.raises(perr.ProviderValidationError):
        validate_model_provider_type("not-a-provider")


def test_build_returns_provider() -> None:
    provider = build_model_provider("openai", http=FakeHttpClient(200, {}))
    assert provider.definition.provider_type == "openai"
