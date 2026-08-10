"""Shared fixtures for provider tests (SQLite, fixture master key)."""

from __future__ import annotations

import pytest
from app.auth.models import User
from app.auth.repository import UserRepository
from app.config import Settings
from app.credentials.crypto import master_key_from_env_value
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault
from app.infra.db import Base
from app.providers.repository import ModelConfigRepository, SearchConfigRepository
from app.providers.service import ProviderService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

FIXTURE_MASTER_KEY = "ab" * 32


def _build_service(tmp_path: object) -> tuple[ProviderService, DbSession]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'prov.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'prov.db'}",
        credential_master_key=FIXTURE_MASTER_KEY,
        credential_key_version="k1",
    )
    vault = CredentialVault(
        master_key=master_key_from_env_value(settings.credential_master_key),
        key_version=settings.credential_key_version,
        repository=CredentialRepository(db),
    )
    service = ProviderService(
        vault=vault,
        model_configs=ModelConfigRepository(db),
        search_configs=SearchConfigRepository(db),
    )
    return service, db


@pytest.fixture()
def service_and_db(tmp_path) -> tuple[ProviderService, DbSession]:
    service, db = _build_service(tmp_path)
    yield service, db
    db.close()


@pytest.fixture()
def two_users(tmp_path) -> tuple[ProviderService, DbSession, User, User]:
    service, db = _build_service(tmp_path)
    users = UserRepository(db)
    alice = users.create("alice@example.com", "hash", None)
    bob = users.create("bob@example.com", "hash", None)
    yield service, db, alice, bob
    db.close()
