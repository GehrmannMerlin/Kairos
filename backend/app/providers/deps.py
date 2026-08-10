"""FastAPI dependencies for provider service + credential vault."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session as DbSession

from app.config import Settings, get_settings
from app.credentials import crypto
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault
from app.infra.deps import get_db
from app.providers.repository import ModelConfigRepository, SearchConfigRepository
from app.providers.service import ProviderService


def get_credential_vault(
    db: DbSession = Depends(get_db), settings: Settings = Depends(get_settings)
) -> CredentialVault:
    return CredentialVault(
        master_key=crypto.master_key_from_env_value(settings.credential_master_key),
        key_version=settings.credential_key_version,
        repository=CredentialRepository(db),
    )


def get_provider_service(
    vault: CredentialVault = Depends(get_credential_vault),
    db: DbSession = Depends(get_db),
) -> ProviderService:
    return ProviderService(
        vault=vault,
        model_configs=ModelConfigRepository(db),
        search_configs=SearchConfigRepository(db),
    )
