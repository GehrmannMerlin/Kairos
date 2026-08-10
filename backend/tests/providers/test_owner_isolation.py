"""Cross-user provider/config isolation (M-03)."""

from __future__ import annotations

import pytest
from app.auth import errors as aerr


def test_b_cannot_see_or_touch_a_model_config(two_users) -> None:
    service, _, alice, bob = two_users
    cfg = service.create_model_config(
        alice,
        name="a",
        provider_type="openai",
        model_name="gpt-4o-mini",
        base_url=None,
        api_key="sk-a",
    )
    assert all(c.config_id != cfg.config_id for c in service.list_model_configs(bob))
    with pytest.raises(aerr.NotFoundError):
        service.update_model_config(
            bob,
            config_id=cfg.config_id,
            name="hack",
            provider_type="openai",
            model_name="x",
            base_url=None,
        )
    with pytest.raises(aerr.NotFoundError):
        service.get_model_config_version(bob, config_id=cfg.config_id, version=1)
    with pytest.raises(aerr.NotFoundError):
        service.delete_model_config(bob, config_id=cfg.config_id)


def test_b_cannot_replace_a_key(two_users) -> None:
    service, _, alice, bob = two_users
    cfg = service.create_model_config(
        alice,
        name="a",
        provider_type="anthropic",
        model_name="claude-3-5-sonnet",
        base_url=None,
        api_key="alice-key-777",
    )
    with pytest.raises(aerr.NotFoundError):
        service.replace_model_api_key(bob, config_id=cfg.config_id, api_key="bob-key")


@pytest.mark.asyncio
async def test_b_cannot_use_a_credential(two_users) -> None:
    service, _, alice, bob = two_users
    cfg = service.create_model_config(
        alice,
        name="a",
        provider_type="anthropic",
        model_name="claude-3-5-sonnet",
        base_url=None,
        api_key="alice-key-777",
    )
    with pytest.raises(aerr.NotFoundError):
        await service.test_model_connection(bob, config_id=cfg.config_id)


def test_b_cannot_touch_a_search_config(two_users) -> None:
    service, _, alice, bob = two_users
    cfg = service.create_search_config(
        alice,
        name="s",
        provider_type="custom_compatible_search",
        base_url="http://search:9000",
        api_key="sk-alice-search",
    )
    assert all(c.config_id != cfg.config_id for c in service.list_search_configs(bob))
    with pytest.raises(aerr.NotFoundError):
        service.delete_search_config(bob, config_id=cfg.config_id)
