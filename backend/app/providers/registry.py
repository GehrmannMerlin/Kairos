"""Code-registered, typed provider registry (M-03).

Providers are registered here in code — never from DB class paths. The registry
exposes metadata so the frontend/service render forms without per-provider
if/else.
"""

from __future__ import annotations

from app.providers import errors
from app.providers.adapters.anthropic import AnthropicModelProvider
from app.providers.adapters.custom_compatible_search import CustomCompatibleSearchProvider
from app.providers.adapters.gemini import GeminiModelProvider
from app.providers.adapters.ollama import OllamaModelProvider
from app.providers.adapters.openai_compatible import OpenAICompatibleModelProvider
from app.providers.protocol import ModelProvider, ProviderDefinition
from app.providers.search_protocol import SearchProvider
from app.providers.transport import HttpClient

_OPENAI_COMPATIBLE_DEFS: list[ProviderDefinition] = [
    ProviderDefinition(
        provider_type="openai",
        display_name="OpenAI",
        requires_api_key=True,
        requires_model_name=True,
        requires_base_url=False,
        default_base_url="https://api.openai.com/v1",
        protocol_family="openai_compatible",
    ),
    ProviderDefinition(
        provider_type="deepseek",
        display_name="DeepSeek",
        requires_api_key=True,
        requires_model_name=True,
        requires_base_url=False,
        default_base_url="https://api.deepseek.com/v1",
        protocol_family="openai_compatible",
    ),
    ProviderDefinition(
        provider_type="openrouter",
        display_name="OpenRouter",
        requires_api_key=True,
        requires_model_name=True,
        requires_base_url=False,
        default_base_url="https://openrouter.ai/api/v1",
        protocol_family="openai_compatible",
    ),
    ProviderDefinition(
        provider_type="custom_openai_compatible",
        display_name="Custom OpenAI-compatible",
        requires_api_key=True,
        requires_model_name=True,
        requires_base_url=True,
        default_base_url=None,
        protocol_family="openai_compatible",
    ),
]

_MODEL_PROVIDER_BUILDERS: dict[str, type] = {
    "openai": OpenAICompatibleModelProvider,
    "deepseek": OpenAICompatibleModelProvider,
    "openrouter": OpenAICompatibleModelProvider,
    "custom_openai_compatible": OpenAICompatibleModelProvider,
    "anthropic": AnthropicModelProvider,
    "gemini": GeminiModelProvider,
    "ollama": OllamaModelProvider,
}


def list_model_provider_definitions() -> list[ProviderDefinition]:
    return [
        *_OPENAI_COMPATIBLE_DEFS,
        AnthropicModelProvider.definition,
        GeminiModelProvider.definition,
        OllamaModelProvider.definition,
    ]


def validate_model_provider_type(provider_type: str) -> None:
    if provider_type not in _MODEL_PROVIDER_BUILDERS:
        raise errors.ProviderValidationError(f"不支持的模型 Provider: {provider_type}")


def build_model_provider(provider_type: str, http: HttpClient | None = None) -> ModelProvider:
    validate_model_provider_type(provider_type)
    if provider_type in ("openai", "deepseek", "openrouter", "custom_openai_compatible"):
        definition = next(d for d in _OPENAI_COMPATIBLE_DEFS if d.provider_type == provider_type)
        return OpenAICompatibleModelProvider(definition, http)
    builder = _MODEL_PROVIDER_BUILDERS[provider_type]
    return builder(http)


_SEARCH_PROVIDER_BUILDERS: dict[str, type] = {
    "custom_compatible_search": CustomCompatibleSearchProvider,
}


def list_search_provider_definitions() -> list[ProviderDefinition]:
    return [CustomCompatibleSearchProvider.definition]


def validate_search_provider_type(provider_type: str) -> None:
    if provider_type not in _SEARCH_PROVIDER_BUILDERS:
        raise errors.ProviderValidationError(f"不支持的搜索 Provider: {provider_type}")


def build_search_provider(provider_type: str, http: HttpClient | None = None) -> SearchProvider:
    validate_search_provider_type(provider_type)
    builder = _SEARCH_PROVIDER_BUILDERS[provider_type]
    return builder(http)
