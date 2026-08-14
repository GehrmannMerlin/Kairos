"""Model/Search config versioning semantics (M-03)."""

from __future__ import annotations

import pytest
from app.auth.models import User
from app.auth.repository import UserRepository
from app.providers import errors
from app.providers.service import ProviderService
from sqlalchemy.orm import Session as DbSession


def _user(db: DbSession, email: str) -> User:
    return UserRepository(db).create(email, "hash", None)


def test_edit_creates_new_version(service_and_db: tuple[ProviderService, DbSession]) -> None:
    service, db = service_and_db
    user = _user(db, "alice@example.com")
    created = service.create_model_config(
        user,
        name="main",
        provider_type="openai",
        model_name="gpt-4o-mini",
        base_url=None,
        api_key="sk-abc",
    )
    edited = service.update_model_config(
        user,
        config_id=created.config_id,
        name="main-2",
        provider_type="openai",
        model_name="gpt-4o",
        base_url=None,
    )
    assert edited.config_id == created.config_id
    assert edited.version == 2
    assert edited.model_name == "gpt-4o"
    old = service.get_model_config_version(user, config_id=created.config_id, version=1)
    assert old.model_name == "gpt-4o-mini"


def test_edit_cannot_reuse_a_credential_with_another_provider(
    service_and_db: tuple[ProviderService, DbSession],
) -> None:
    service, db = service_and_db
    user = _user(db, "alice@example.com")
    created = service.create_model_config(
        user,
        name="openai",
        provider_type="openai",
        model_name="gpt-4o-mini",
        base_url=None,
        api_key="stored-openai-fixture-key",
    )

    with pytest.raises(errors.ProviderValidationError, match="Provider"):
        service.update_model_config(
            user,
            config_id=created.config_id,
            name="deepseek",
            provider_type="deepseek",
            model_name="deepseek-v4-flash",
            base_url=None,
        )

    current = service.get_model_config_version(user, config_id=created.config_id, version=1)
    assert current.provider_type == "openai"


def test_replace_key_bumps_credential_and_config_version(
    service_and_db: tuple[ProviderService, DbSession],
) -> None:
    service, db = service_and_db
    user = _user(db, "alice@example.com")
    created = service.create_model_config(
        user,
        name="main",
        provider_type="anthropic",
        model_name="claude-3-5-sonnet",
        base_url=None,
        api_key="key-v1",
    )
    replaced = service.replace_model_api_key(user, config_id=created.config_id, api_key="key-v2")
    assert replaced.version == 2
    cred_v1 = service.get_model_config_version(
        user, config_id=created.config_id, version=1
    ).credential_version_id
    cred_v2 = service.get_model_config_version(
        user, config_id=replaced.config_id, version=2
    ).credential_version_id
    assert cred_v1 is not None
    assert cred_v2 is not None
    assert cred_v1 != cred_v2


def test_set_default_only_changes_current(
    service_and_db: tuple[ProviderService, DbSession],
) -> None:
    service, db = service_and_db
    user = _user(db, "alice@example.com")
    a = service.create_model_config(
        user,
        name="a",
        provider_type="openai",
        model_name="gpt-4o-mini",
        base_url=None,
        api_key=None,
    )
    service.create_model_config(
        user,
        name="b",
        provider_type="openai",
        model_name="gpt-4o-mini",
        base_url=None,
        api_key=None,
    )
    service.set_default_model(user, config_id=a.config_id)
    default = service.get_default_model(user)
    assert default is not None
    assert default.config_id == a.config_id


def test_search_config_versioning(service_and_db: tuple[ProviderService, DbSession]) -> None:
    service, db = service_and_db
    user = _user(db, "alice@example.com")
    created = service.create_search_config(
        user,
        name="search-1",
        provider_type="custom_compatible_search",
        base_url="http://search:9000",
        api_key="sk-search",
    )
    updated = service.update_search_config(
        user,
        config_id=created.config_id,
        name="search-2",
        provider_type="custom_compatible_search",
        base_url="http://search:9001",
    )
    assert updated.version == 2
    assert updated.base_url == "http://search:9001"
