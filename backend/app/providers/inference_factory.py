"""One settings-aware construction path for model inference clients."""

from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.providers.inference import ModelInferenceClient
from app.providers.inference_policy import InferenceIntent
from app.providers.protocol import ProviderDefinition
from app.providers.registry import get_model_definition
from app.providers.transport import HttpClient


def build_inference_client(
    *,
    intent: InferenceIntent,
    settings: Settings,
    http: HttpClient | None = None,
    definition_resolver: Callable[[str], ProviderDefinition] = get_model_definition,
) -> ModelInferenceClient:
    return ModelInferenceClient(
        intent=intent,
        settings=settings,
        http=http,
        timeout_seconds=settings.provider_inference_timeout_seconds,
        definition_resolver=definition_resolver,
    )
