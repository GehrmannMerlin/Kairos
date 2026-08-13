"""Model/Search provider contracts (M-03). Model and Search stay separate DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ProviderTestStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    AUTH_FAILED = "AUTH_FAILED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    FAILED = "FAILED"


class BaseUrlMode(StrEnum):
    """How a provider's Base URL is resolved (Registry is the source of truth).

    ``managed``        — built-in endpoint managed by the Registry; users never
                         type a Base URL (OpenAI/DeepSeek/OpenRouter/Anthropic/Gemini).
    ``required``       — user must supply a Base URL (custom OpenAI-compatible).
    ``local_required`` — user must supply a Base URL, no API key (Ollama).
    """

    MANAGED = "managed"
    REQUIRED = "required"
    LOCAL_REQUIRED = "local_required"


class DetectionConfidence(StrEnum):
    """Key fingerprint confidence. AMBIGUOUS/NONE never trigger a network probe."""

    HIGH = "HIGH"
    AMBIGUOUS = "AMBIGUOUS"
    NONE = "NONE"


@dataclass(frozen=True)
class ProviderTestResult:
    status: ProviderTestStatus
    error_code: str | None = None
    message: str | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class ModelProbeResult:
    """Result of probing an *unsaved* model provider key (never persisted).

    ``status`` is ``None`` when no network request was attempted (AMBIGUOUS/NONE
    fingerprint, or a validation stop such as a missing Base URL / API key).
    """

    status: ProviderTestStatus | None
    detection_confidence: DetectionConfidence
    detected_provider: str | None
    candidates: tuple[str, ...] = ()
    resolved_base_url: str | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    message: str | None = None
    probe_method: str | None = None  # "fingerprint" | "manual" | None


@dataclass(frozen=True)
class ProviderDefinition:
    provider_type: str
    display_name: str
    requires_api_key: bool
    requires_model_name: bool
    requires_base_url: bool
    default_base_url: str | None
    protocol_family: str
    base_url_mode: BaseUrlMode


@dataclass(frozen=True)
class ResolvedModel:
    """Stable, serializable descriptor M-06/M-11 map to a real agent model."""

    provider_type: str
    model_name: str
    base_url: str | None
    credential_version_id: int | None


class ModelProvider(Protocol):
    definition: ProviderDefinition

    async def test_connection(
        self, *, api_key: str | None, model: str | None, base_url: str | None
    ) -> ProviderTestResult: ...

    def resolve_model(
        self, *, model: str, base_url: str | None, credential_version_id: int | None
    ) -> ResolvedModel: ...
