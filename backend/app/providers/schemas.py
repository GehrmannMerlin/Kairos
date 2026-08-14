"""Provider API DTOs (M-03).

Responses never contain secret/ciphertext/wrapped key/nonce/master key. api_key
is write-only via SecretStr.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, SecretStr

from app.providers.protocol import DetectionConfidence, ProviderTestStatus


class ProviderDefinitionDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    provider_type: str
    display_name: str
    requires_api_key: bool
    requires_model_name: bool
    requires_base_url: bool
    default_base_url: str | None
    protocol_family: str
    base_url_mode: str


class ModelConfigDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    config_id: str
    version: int
    name: str
    provider_type: str
    model_name: str
    base_url: str | None
    credential_configured: bool
    is_default: bool
    connection_status: str
    last_tested_at: datetime | None
    created_at: datetime


class CreateModelConfigCommand(BaseModel):
    name: str
    provider_type: str
    model_name: str
    base_url: str | None = None
    api_key: SecretStr | None = None
    set_default: bool = False


class UpdateModelConfigCommand(BaseModel):
    name: str
    provider_type: str
    model_name: str
    base_url: str | None = None


class ReplaceKeyCommand(BaseModel):
    api_key: SecretStr


class ModelConfigListResponse(BaseModel):
    configs: list[ModelConfigDto]
    definitions: list[ProviderDefinitionDto]


class ProviderTestResultDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: ProviderTestStatus
    error_code: str | None = None
    message: str | None = None
    latency_ms: int | None = None


class ModelProbeCommand(BaseModel):
    """Unsaved probe payload. api_key is write-only and never persisted/echoed."""

    api_key: SecretStr | None = None
    provider_type: str | None = None
    base_url: str | None = None
    model_name: str | None = None


class ModelProbeResultDto(BaseModel):
    """Desensitized probe result. Never contains the api_key or raw responses."""

    status: ProviderTestStatus | None = None
    detection_confidence: DetectionConfidence
    detected_provider: str | None = None
    candidates: list[str] = []
    resolved_base_url: str | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    message: str | None = None
    probe_method: str | None = None


class ModelCatalogCommand(BaseModel):
    """Load IDs from one explicitly selected provider without persisting a key."""

    provider_type: str
    api_key: SecretStr | None = None
    base_url: str | None = None
    config_id: str | None = None


class ModelCatalogResultDto(BaseModel):
    status: ProviderTestStatus
    models: list[str] = []
    resolved_base_url: str | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    message: str | None = None


class SearchConfigDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    config_id: str
    version: int
    name: str
    provider_type: str
    base_url: str | None
    credential_configured: bool
    connection_status: str
    last_tested_at: datetime | None
    created_at: datetime


class CreateSearchConfigCommand(BaseModel):
    name: str
    provider_type: str
    base_url: str | None = None
    api_key: SecretStr | None = None


class UpdateSearchConfigCommand(BaseModel):
    name: str
    provider_type: str
    base_url: str | None = None


class SearchProbeCommand(BaseModel):
    """Unsaved search probe payload. api_key is write-only, never persisted.

    ``provider_type`` is required — search providers are always selected
    explicitly by the user (no key fingerprint stage like Model probe).
    """

    provider_type: str
    api_key: SecretStr | None = None
    base_url: str | None = None


class SearchProbeResultDto(BaseModel):
    """Desensitized search probe result. Never contains the api_key or the raw
    third-party response body."""

    status: ProviderTestStatus | None = None
    provider_type: str
    resolved_base_url: str | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    message: str | None = None


class SearchConfigListResponse(BaseModel):
    configs: list[SearchConfigDto]
    definitions: list[ProviderDefinitionDto]
