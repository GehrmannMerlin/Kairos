"""CredentialVault behavior against SQLite."""

from __future__ import annotations

import pytest
from app.auth import errors
from app.auth.models import User
from app.auth.repository import UserRepository
from app.config import Settings
from app.credentials import crypto
from app.credentials.models import CredentialVersion
from app.credentials.repository import CredentialRepository
from app.credentials.vault import CredentialVault
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def vault_and_db(tmp_path) -> tuple[CredentialVault, DbSession]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'vault.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'vault.db'}",
        credential_master_key="ab" * 32,
        credential_key_version="k1",
    )
    vault = CredentialVault(
        master_key=crypto.master_key_from_env_value(settings.credential_master_key),
        key_version=settings.credential_key_version,
        repository=CredentialRepository(db),
    )
    yield vault, db
    db.close()


def _user(db: DbSession, email: str) -> User:
    return UserRepository(db).create(email, "hashed-not-used-in-this-test", None)


def test_store_secret_and_read_for_execution(
    vault_and_db: tuple[CredentialVault, DbSession],
) -> None:
    vault, db = vault_and_db
    user = _user(db, "alice@example.com")
    info = vault.store_secret(
        user_id=user.id, kind="model_api_key", name="openai", secret="sk-live-abc"
    )
    assert info.credential_id
    assert info.version == 1
    assert (
        vault.read_for_execution(user_id=user.id, credential_version_id=info.version_id)
        == "sk-live-abc"
    )


def test_db_never_stores_plaintext(vault_and_db: tuple[CredentialVault, DbSession]) -> None:
    vault, db = vault_and_db
    user = _user(db, "alice@example.com")
    vault.store_secret(
        user_id=user.id, kind="model_api_key", name="openai", secret="sk-super-secret-999"
    )
    rows = db.query(CredentialVersion).all()
    text = repr([{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows])
    assert "sk-super-secret-999" not in text


def test_rotate_keeps_identity_and_versions(
    vault_and_db: tuple[CredentialVault, DbSession],
) -> None:
    vault, db = vault_and_db
    user = _user(db, "alice@example.com")
    first = vault.store_secret(
        user_id=user.id, kind="model_api_key", name="openai", secret="v1-secret"
    )
    second = vault.rotate(user_id=user.id, credential_id=first.credential_id, secret="v2-secret")
    assert second.credential_id == first.credential_id
    assert second.version == 2
    assert first.version == 1
    assert (
        vault.read_for_execution(user_id=user.id, credential_version_id=second.version_id)
        == "v2-secret"
    )


def test_cross_user_cannot_read(vault_and_db: tuple[CredentialVault, DbSession]) -> None:
    vault, db = vault_and_db
    alice = _user(db, "alice@example.com")
    bob = _user(db, "bob@example.com")
    info = vault.store_secret(
        user_id=alice.id, kind="model_api_key", name="openai", secret="alice-key"
    )
    with pytest.raises(errors.NotFoundError):
        vault.read_for_execution(user_id=bob.id, credential_version_id=info.version_id)
    with pytest.raises(errors.NotFoundError):
        vault.rotate(user_id=bob.id, credential_id=info.credential_id, secret="x")


def test_revoke_zeroes_ciphertext_and_disables(
    vault_and_db: tuple[CredentialVault, DbSession],
) -> None:
    vault, db = vault_and_db
    user = _user(db, "alice@example.com")
    info = vault.store_secret(
        user_id=user.id, kind="model_api_key", name="openai", secret="to-revoke"
    )
    vault.revoke(user_id=user.id, credential_id=info.credential_id)
    row = db.query(CredentialVersion).filter(CredentialVersion.id == info.version_id).one()
    assert row.status == "retired"
    assert row.secret_ciphertext == b""
    with pytest.raises(crypto.CredentialError):
        vault.read_for_execution(user_id=user.id, credential_version_id=info.version_id)
