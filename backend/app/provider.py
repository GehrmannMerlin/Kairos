from __future__ import annotations

import os
from typing import Any

from pydantic_ai.capabilities import ResolveModelId
from pydantic_ai.models import Model, ModelResolutionContext, infer_model
from pydantic_ai.providers import infer_provider

from app.agent.deps import KairosAgentDeps
from app.config import get_settings


class ProviderCredentialError(RuntimeError):
    """The worker has no credential for the configured model provider."""


def _provider_factory(provider_name: str) -> Any:
    settings = get_settings()
    credential = os.getenv(settings.model_credential_env)
    if not credential:
        raise ProviderCredentialError(
            "No credential is configured for model provider; set "
            f"{settings.model_credential_env} in the worker environment"
        )
    if provider_name == "openai":
        from pydantic_ai.providers.openai import OpenAIProvider

        return OpenAIProvider(api_key=credential)
    return infer_provider(provider_name)


def resolve_model_id(ctx: ModelResolutionContext[KairosAgentDeps], model_id: str) -> Model | None:
    """Resolve Kairos model aliases without carrying the credential across Temporal."""
    expected_alias = f"kairos:{ctx.deps.model_config_id}"
    if model_id != expected_alias:
        return None
    settings = get_settings()
    return infer_model(settings.model_id, provider_factory=_provider_factory)


model_resolver_capability = ResolveModelId(resolve_model_id)
