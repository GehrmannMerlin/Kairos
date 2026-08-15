"""FastAPI dependencies for the Goal Understanding Agent (M-06)."""

from __future__ import annotations

from app.agents.goal_understanding import GoalUnderstandingAgent
from app.agents.service import GoalUnderstandingService
from app.config import Settings, get_settings
from app.credentials.vault import CredentialVault
from app.infra.deps import get_db
from app.providers.deps import get_credential_vault, get_provider_service
from app.providers.service import ProviderService
from fastapi import Depends
from sqlalchemy.orm import Session as DbSession


def get_goal_understanding_service(
    db: DbSession = Depends(get_db),
    provider_service: ProviderService = Depends(get_provider_service),
    vault: CredentialVault = Depends(get_credential_vault),
    settings: Settings = Depends(get_settings),
) -> GoalUnderstandingService:
    return GoalUnderstandingService(
        db,
        provider_service=provider_service,
        vault=vault,
        agent=GoalUnderstandingAgent(settings=settings),
    )
