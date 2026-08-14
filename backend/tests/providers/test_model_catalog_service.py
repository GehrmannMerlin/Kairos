"""Owner-scoped model catalog service behavior."""

from __future__ import annotations

import pytest
from app.auth import errors as auth_errors
from app.credentials.models import CredentialVersion, ModelConfig
from app.providers import errors
from app.providers.protocol import ProviderTestStatus
from tests.providers.fake_transport import FakeHttpClient


def _catalog_body(*ids: str) -> dict:
    return {
        "object": "list",
        "data": [{"id": model_id, "object": "model", "owned_by": "fixture"} for model_id in ids],
    }


@pytest.mark.asyncio
async def test_transient_catalog_key_is_not_persisted(probe_factory) -> None:
    fake = FakeHttpClient(200, _catalog_body("deepseek-v4-flash", "deepseek-v4-pro"))
    service, db, user = probe_factory(fake)

    result = await service.list_available_models(
        user,
        provider_type="deepseek",
        api_key="transient-fixture-key",
        base_url=None,
        config_id=None,
    )

    assert result.status is ProviderTestStatus.AVAILABLE
    assert result.models == ("deepseek-v4-flash", "deepseek-v4-pro")
    assert db.query(CredentialVersion).count() == 0
    assert db.query(ModelConfig).count() == 0


@pytest.mark.asyncio
async def test_existing_catalog_uses_current_owned_credential_version(probe_factory) -> None:
    fake = FakeHttpClient(200, _catalog_body("deepseek-v4-flash"))
    service, _, user = probe_factory(fake)
    created = service.create_model_config(
        user,
        name="deepseek",
        provider_type="deepseek",
        model_name="deepseek-v4-flash",
        base_url=None,
        api_key="fixture-key-v1",
    )
    current = service.replace_model_api_key(
        user, config_id=created.config_id, api_key="fixture-key-v2"
    )

    result = await service.list_available_models(
        user,
        provider_type="deepseek",
        api_key=None,
        base_url=None,
        config_id=current.config_id,
    )

    assert result.models == ("deepseek-v4-flash",)
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer fixture-key-v2"


@pytest.mark.asyncio
async def test_catalog_rejects_cross_user_config_id(two_users) -> None:
    service, _, alice, bob = two_users
    created = service.create_model_config(
        alice,
        name="alice",
        provider_type="deepseek",
        model_name="deepseek-v4-flash",
        base_url=None,
        api_key="alice-fixture-key",
    )

    with pytest.raises(auth_errors.NotFoundError):
        await service.list_available_models(
            bob,
            provider_type="deepseek",
            api_key=None,
            base_url=None,
            config_id=created.config_id,
        )


@pytest.mark.asyncio
async def test_catalog_never_sends_a_saved_credential_to_a_different_provider(
    probe_factory,
) -> None:
    fake = FakeHttpClient(200, _catalog_body("gpt-5-mini"))
    service, _, user = probe_factory(fake)
    created = service.create_model_config(
        user,
        name="deepseek",
        provider_type="deepseek",
        model_name="deepseek-v4-flash",
        base_url=None,
        api_key="stored-deepseek-fixture-key",
    )

    with pytest.raises(errors.ProviderValidationError, match="Provider"):
        await service.list_available_models(
            user,
            provider_type="openai",
            api_key=None,
            base_url=None,
            config_id=created.config_id,
        )

    assert fake.calls == []


@pytest.mark.asyncio
async def test_custom_catalog_requires_valid_base_url(probe_factory) -> None:
    service, _, user = probe_factory(FakeHttpClient(200, _catalog_body("custom-model")))

    with pytest.raises(errors.ProviderValidationError):
        await service.list_available_models(
            user,
            provider_type="custom_openai_compatible",
            api_key="fixture-key",
            base_url=None,
            config_id=None,
        )


@pytest.mark.asyncio
async def test_catalog_rejects_two_credential_sources(probe_factory) -> None:
    service, _, user = probe_factory(FakeHttpClient(200, _catalog_body("deepseek-v4-flash")))
    created = service.create_model_config(
        user,
        name="deepseek",
        provider_type="deepseek",
        model_name="deepseek-v4-flash",
        base_url=None,
        api_key="stored-fixture-key",
    )

    with pytest.raises(errors.ProviderValidationError):
        await service.list_available_models(
            user,
            provider_type="deepseek",
            api_key="transient-fixture-key",
            base_url=None,
            config_id=created.config_id,
        )


@pytest.mark.asyncio
async def test_saved_connection_uses_the_same_injected_catalog_transport(
    probe_factory, monkeypatch
) -> None:
    """Catches test/inference parity drift back to a separately built transport."""
    fake = FakeHttpClient(200, _catalog_body("deepseek-v4-flash", "deepseek-v4-pro"))
    service, _, user = probe_factory(fake)
    created = service.create_model_config(
        user,
        name="legacy",
        provider_type="deepseek",
        model_name="DeepSeek",
        base_url=None,
        api_key="fixture-key",
    )

    from app.providers import service as service_module

    real_builder = service_module.build_model_provider

    def guarded_builder(provider_type: str, http=None):
        assert http is fake
        return real_builder(provider_type, http=http)

    monkeypatch.setattr(service_module, "build_model_provider", guarded_builder)

    result = await service.test_model_connection(user, config_id=created.config_id)

    assert result.status is ProviderTestStatus.MODEL_NOT_FOUND
