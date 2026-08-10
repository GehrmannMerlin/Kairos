"""MODEL_NOT_CONFIGURED / SEARCH_PROVIDER_NOT_CONFIGURED guards."""

from __future__ import annotations

import pytest
from app.providers import errors


def test_guard_errors_have_stable_codes() -> None:
    assert errors.ModelNotConfiguredError("x").code == "MODEL_NOT_CONFIGURED"
    assert errors.SearchProviderNotConfiguredError("x").code == "SEARCH_PROVIDER_NOT_CONFIGURED"
    assert errors.ModelNotConfiguredError("x").status_code == 409


def test_no_default_model_raises(two_users) -> None:
    service, _, _, bob = two_users
    with pytest.raises(errors.ModelNotConfiguredError):
        service.require_available_model_config(bob)


def test_untested_default_still_raises(two_users) -> None:
    service, _, alice, _ = two_users
    service.create_model_config(
        alice,
        name="main",
        provider_type="openai",
        model_name="gpt-4o-mini",
        base_url=None,
        api_key=None,
        set_default=True,
    )
    with pytest.raises(errors.ModelNotConfiguredError):
        service.require_available_model_config(alice)


def test_no_search_config_raises(two_users) -> None:
    service, _, _, bob = two_users
    with pytest.raises(errors.SearchProviderNotConfiguredError):
        service.require_available_search_config(bob)


def test_available_search_passes(two_users) -> None:
    service, db, alice, _ = two_users
    # mark a search config as available directly through its current row
    cfg = service.create_search_config(
        alice,
        name="s",
        provider_type="custom_compatible_search",
        base_url="http://search:9000",
        api_key="sk",
    )
    from datetime import UTC, datetime

    service._search_configs.mark_connection(alice.id, cfg.config_id, "available", datetime.now(UTC))
    assert service.require_available_search_config(alice).config_id == cfg.config_id


def test_available_default_model_passes(two_users) -> None:
    service, _, alice, _ = two_users
    from datetime import UTC, datetime

    cfg = service.create_model_config(
        alice,
        name="main",
        provider_type="openai",
        model_name="gpt-4o-mini",
        base_url=None,
        api_key=None,
        set_default=True,
    )
    service._model_configs.mark_connection(alice.id, cfg.config_id, "available", datetime.now(UTC))
    assert service.require_available_model_config(alice).config_id == cfg.config_id
