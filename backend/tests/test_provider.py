import os

from app.config import get_settings
from app.provider import _provider_factory
from pydantic_ai.providers.deepseek import DeepSeekProvider


def test_default_model_is_low_cost_deepseek_chat() -> None:
    settings = get_settings()

    assert settings.model_id == "deepseek:deepseek-chat"
    assert settings.model_credential_env == "DEEPSEEK_API_KEY"


def test_provider_factory_builds_public_deepseek_provider_without_network(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-only-not-a-real-secret")

    provider = _provider_factory("deepseek")

    assert isinstance(provider, DeepSeekProvider)
    assert provider.base_url == "https://api.deepseek.com"
    assert os.getenv("DEEPSEEK_API_KEY") == "unit-test-only-not-a-real-secret"
