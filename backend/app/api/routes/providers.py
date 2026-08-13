"""Provider configuration API (M-03). Thin layer: DTO -> service -> DTO.

No SQL, no credential decryption, no direct SDK calls here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.deps import require_user
from app.auth.models import User
from app.credentials.models import ModelConfig, SearchConfig
from app.providers.deps import get_provider_service
from app.providers.registry import (
    list_model_provider_definitions,
    list_search_provider_definitions,
)
from app.providers.schemas import (
    CreateModelConfigCommand,
    CreateSearchConfigCommand,
    ModelConfigDto,
    ModelConfigListResponse,
    ModelProbeCommand,
    ModelProbeResultDto,
    ProviderDefinitionDto,
    ProviderTestResultDto,
    ReplaceKeyCommand,
    SearchConfigDto,
    SearchConfigListResponse,
    SearchProbeCommand,
    SearchProbeResultDto,
    UpdateModelConfigCommand,
    UpdateSearchConfigCommand,
)
from app.providers.service import ProviderService

router = APIRouter(prefix="/providers", tags=["providers"])


def _model_dto(row: ModelConfig) -> ModelConfigDto:
    return ModelConfigDto(
        config_id=row.config_id,
        version=row.version,
        name=row.name,
        provider_type=row.provider_type,
        model_name=row.model_name,
        base_url=row.base_url,
        credential_configured=row.credential_version_id is not None,
        is_default=row.is_default,
        connection_status=row.connection_status,
        last_tested_at=row.last_tested_at,
        created_at=row.created_at,
    )


def _search_dto(row: SearchConfig) -> SearchConfigDto:
    return SearchConfigDto(
        config_id=row.config_id,
        version=row.version,
        name=row.name,
        provider_type=row.provider_type,
        base_url=row.base_url,
        credential_configured=row.credential_version_id is not None,
        connection_status=row.connection_status,
        last_tested_at=row.last_tested_at,
        created_at=row.created_at,
    )


@router.get("/definitions")
def definitions() -> dict:
    return {
        "models": [
            ProviderDefinitionDto.model_validate(d) for d in list_model_provider_definitions()
        ],
        "searches": [
            ProviderDefinitionDto.model_validate(d) for d in list_search_provider_definitions()
        ],
    }


@router.get("/models", response_model=ModelConfigListResponse)
def list_models(
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> ModelConfigListResponse:
    configs = [_model_dto(c) for c in service.list_model_configs(user)]
    return ModelConfigListResponse(
        configs=configs,
        definitions=[
            ProviderDefinitionDto.model_validate(d) for d in list_model_provider_definitions()
        ],
    )


@router.post("/models/probe", response_model=ModelProbeResultDto)
async def probe_model(
    cmd: ModelProbeCommand,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> ModelProbeResultDto:
    result = await service.probe_model(
        api_key=cmd.api_key.get_secret_value() if cmd.api_key else None,
        provider_type=cmd.provider_type,
        base_url=cmd.base_url,
        model_name=cmd.model_name,
    )
    return ModelProbeResultDto(
        status=result.status,
        detection_confidence=result.detection_confidence,
        detected_provider=result.detected_provider,
        candidates=list(result.candidates),
        resolved_base_url=result.resolved_base_url,
        latency_ms=result.latency_ms,
        error_code=result.error_code,
        message=result.message,
        probe_method=result.probe_method,
    )


@router.post("/models", response_model=ModelConfigDto, status_code=201)
def create_model(
    cmd: CreateModelConfigCommand,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> ModelConfigDto:
    row = service.create_model_config(
        user,
        name=cmd.name,
        provider_type=cmd.provider_type,
        model_name=cmd.model_name,
        base_url=cmd.base_url,
        api_key=cmd.api_key.get_secret_value() if cmd.api_key else None,
        set_default=cmd.set_default,
    )
    return _model_dto(row)


@router.patch("/models/{config_id}", response_model=ModelConfigDto)
def update_model(
    config_id: str,
    cmd: UpdateModelConfigCommand,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> ModelConfigDto:
    row = service.update_model_config(
        user,
        config_id=config_id,
        name=cmd.name,
        provider_type=cmd.provider_type,
        model_name=cmd.model_name,
        base_url=cmd.base_url,
    )
    return _model_dto(row)


@router.post("/models/{config_id}/key", response_model=ModelConfigDto)
def replace_model_key(
    config_id: str,
    cmd: ReplaceKeyCommand,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> ModelConfigDto:
    row = service.replace_model_api_key(
        user, config_id=config_id, api_key=cmd.api_key.get_secret_value()
    )
    return _model_dto(row)


@router.post("/models/{config_id}/test", response_model=ProviderTestResultDto)
async def test_model(
    config_id: str,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> ProviderTestResultDto:
    result = await service.test_model_connection(user, config_id=config_id)
    return ProviderTestResultDto.model_validate(result)


@router.post("/models/{config_id}/default", response_model=ModelConfigDto)
def set_default_model(
    config_id: str,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> ModelConfigDto:
    row = service.set_default_model(user, config_id=config_id)
    return _model_dto(row)


@router.delete("/models/{config_id}", status_code=204)
def delete_model(
    config_id: str,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> None:
    service.delete_model_config(user, config_id=config_id)
    return None


@router.get("/searches", response_model=SearchConfigListResponse)
def list_searches(
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> SearchConfigListResponse:
    configs = [_search_dto(c) for c in service.list_search_configs(user)]
    return SearchConfigListResponse(
        configs=configs,
        definitions=[
            ProviderDefinitionDto.model_validate(d) for d in list_search_provider_definitions()
        ],
    )


@router.post("/searches/probe", response_model=SearchProbeResultDto)
async def probe_search(
    cmd: SearchProbeCommand,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> SearchProbeResultDto:
    result = await service.probe_search(
        provider_type=cmd.provider_type,
        api_key=cmd.api_key.get_secret_value() if cmd.api_key else None,
        base_url=cmd.base_url,
    )
    return SearchProbeResultDto(
        status=result.status,
        provider_type=result.provider_type,
        resolved_base_url=result.resolved_base_url,
        latency_ms=result.latency_ms,
        error_code=result.error_code,
        message=result.message,
    )


@router.post("/searches", response_model=SearchConfigDto, status_code=201)
def create_search(
    cmd: CreateSearchConfigCommand,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> SearchConfigDto:
    row = service.create_search_config(
        user,
        name=cmd.name,
        provider_type=cmd.provider_type,
        base_url=cmd.base_url,
        api_key=cmd.api_key.get_secret_value() if cmd.api_key else None,
    )
    return _search_dto(row)


@router.patch("/searches/{config_id}", response_model=SearchConfigDto)
def update_search(
    config_id: str,
    cmd: UpdateSearchConfigCommand,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> SearchConfigDto:
    row = service.update_search_config(
        user,
        config_id=config_id,
        name=cmd.name,
        provider_type=cmd.provider_type,
        base_url=cmd.base_url,
    )
    return _search_dto(row)


@router.post("/searches/{config_id}/key", response_model=SearchConfigDto)
def replace_search_key(
    config_id: str,
    cmd: ReplaceKeyCommand,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> SearchConfigDto:
    row = service.replace_search_api_key(
        user, config_id=config_id, api_key=cmd.api_key.get_secret_value()
    )
    return _search_dto(row)


@router.post("/searches/{config_id}/test", response_model=ProviderTestResultDto)
async def test_search(
    config_id: str,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> ProviderTestResultDto:
    result = await service.test_search_connection(user, config_id=config_id)
    return ProviderTestResultDto.model_validate(result)


@router.delete("/searches/{config_id}", status_code=204)
def delete_search(
    config_id: str,
    user: User = Depends(require_user),
    service: ProviderService = Depends(get_provider_service),
) -> None:
    service.delete_search_config(user, config_id=config_id)
    return None
