"""Provider API DTOs (M-03).

Responses never contain secret/ciphertext/wrapped key/nonce/master key. api_key
is write-only via SecretStr.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, SecretStr

from app.providers.protocol import ProviderTestStatus


class ProviderDefinitionDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    provider_type: str
    display_name: str
    requires_api_key: bool
    requires_model_name: bool
    requires_base_url: bool
    default_base_url: str | None
    protocol_family: str


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
    base_url: str
    api_key: SecretStr | None = None


class UpdateSearchConfigCommand(BaseModel):
    name: str
    provider_type: str
    base_url: str


class SearchConfigListResponse(BaseModel):
    configs: list[SearchConfigDto]
    definitions: list[ProviderDefinitionDto]
