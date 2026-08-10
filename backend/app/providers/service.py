"""ProviderService: model/search config lifecycle, connection tests, guards.

Routes stay thin; all credential decryption happens here through
``CredentialVault.read_for_execution`` (controlled execution path only).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.credentials.models import ModelConfig, SearchConfig
from app.credentials.vault import CredentialVault
from app.providers import errors
from app.providers.protocol import ProviderTestResult
from app.providers.registry import (
    build_model_provider,
    build_search_provider,
    validate_model_provider_type,
    validate_search_provider_type,
)
from app.providers.repository import ModelConfigRepository, SearchConfigRepository


def _now() -> datetime:
    return datetime.now(UTC)


class ProviderService:
    def __init__(
        self,
        *,
        vault: CredentialVault,
        model_configs: ModelConfigRepository,
        search_configs: SearchConfigRepository,
    ) -> None:
        self._vault = vault
        self._model_configs = model_configs
        self._search_configs = search_configs

    # ---- Model config lifecycle ----

    def list_model_configs(self, user: Any) -> list[ModelConfig]:
        return self._model_configs.list_current(user.id)

    def create_model_config(
        self,
        user: Any,
        *,
        name: str,
        provider_type: str,
        model_name: str,
        base_url: str | None,
        api_key: str | None,
        set_default: bool = False,
    ) -> ModelConfig:
        validate_model_provider_type(provider_type)
        credential_version_id = None
        if api_key:
            info = self._vault.store_secret(
                user_id=user.id, kind="model_api_key", name=name, secret=api_key
            )
            credential_version_id = info.version_id
        if set_default:
            self._model_configs.clear_defaults(user.id)
        return self._model_configs.create_version(
            user_id=user.id,
            name=name,
            provider_type=provider_type,
            model_name=model_name,
            base_url=base_url,
            credential_version_id=credential_version_id,
            is_default=set_default,
        )

    def update_model_config(
        self,
        user: Any,
        *,
        config_id: str,
        name: str,
        provider_type: str,
        model_name: str,
        base_url: str | None,
    ) -> ModelConfig:
        validate_model_provider_type(provider_type)
        current = self._model_configs.get_current(user.id, config_id)
        return self._model_configs.append_version(
            config_id=config_id,
            user_id=user.id,
            name=name,
            provider_type=provider_type,
            model_name=model_name,
            base_url=base_url,
            credential_version_id=current.credential_version_id,
            is_default=current.is_default,
        )

    def replace_model_api_key(self, user: Any, *, config_id: str, api_key: str) -> ModelConfig:
        current = self._model_configs.get_current(user.id, config_id)
        if current.credential_version_id is not None:
            info = self._vault.rotate_for_config(
                user_id=user.id,
                credential_version_id=current.credential_version_id,
                secret=api_key,
            )
        else:
            info = self._vault.store_secret(
                user_id=user.id, kind="model_api_key", name=current.name, secret=api_key
            )
        return self._model_configs.append_version(
            config_id=config_id,
            user_id=user.id,
            name=current.name,
            provider_type=current.provider_type,
            model_name=current.model_name,
            base_url=current.base_url,
            credential_version_id=info.version_id,
            is_default=current.is_default,
        )

    async def test_model_connection(self, user: Any, *, config_id: str) -> ProviderTestResult:
        current = self._model_configs.get_current(user.id, config_id)
        api_key = None
        if current.credential_version_id is not None:
            api_key = self._vault.read_for_execution(
                user_id=user.id, credential_version_id=current.credential_version_id
            )
        provider = build_model_provider(current.provider_type)
        result = await provider.test_connection(
            api_key=api_key, model=current.model_name, base_url=current.base_url
        )
        self._model_configs.mark_connection(user.id, config_id, result.status.value.lower(), _now())
        return result

    def set_default_model(self, user: Any, *, config_id: str) -> ModelConfig:
        return self._model_configs.set_default(user.id, config_id)

    def delete_model_config(self, user: Any, *, config_id: str) -> None:
        current = self._model_configs.get_current(user.id, config_id)
        if current.credential_version_id is not None:
            self._vault.revoke_by_version(
                user_id=user.id, credential_version_id=current.credential_version_id
            )
        self._model_configs.delete(user.id, config_id)

    def get_model_config_version(self, user: Any, *, config_id: str, version: int) -> ModelConfig:
        return self._model_configs.get_version(user.id, config_id, version)

    def get_default_model(self, user: Any) -> ModelConfig | None:
        return self._model_configs.get_default(user.id)

    # ---- Search config lifecycle ----

    def list_search_configs(self, user: Any) -> list[SearchConfig]:
        return self._search_configs.list_current(user.id)

    def create_search_config(
        self,
        user: Any,
        *,
        name: str,
        provider_type: str,
        base_url: str | None,
        api_key: str | None,
    ) -> SearchConfig:
        validate_search_provider_type(provider_type)
        credential_version_id = None
        if api_key:
            info = self._vault.store_secret(
                user_id=user.id, kind="search_api_key", name=name, secret=api_key
            )
            credential_version_id = info.version_id
        return self._search_configs.create_version(
            user_id=user.id,
            name=name,
            provider_type=provider_type,
            base_url=base_url,
            credential_version_id=credential_version_id,
        )

    def update_search_config(
        self,
        user: Any,
        *,
        config_id: str,
        name: str,
        provider_type: str,
        base_url: str | None,
    ) -> SearchConfig:
        validate_search_provider_type(provider_type)
        current = self._search_configs.get_current(user.id, config_id)
        return self._search_configs.append_version(
            config_id=config_id,
            user_id=user.id,
            name=name,
            provider_type=provider_type,
            base_url=base_url,
            credential_version_id=current.credential_version_id,
        )

    def replace_search_api_key(self, user: Any, *, config_id: str, api_key: str) -> SearchConfig:
        current = self._search_configs.get_current(user.id, config_id)
        if current.credential_version_id is not None:
            info = self._vault.rotate_for_config(
                user_id=user.id,
                credential_version_id=current.credential_version_id,
                secret=api_key,
            )
        else:
            info = self._vault.store_secret(
                user_id=user.id, kind="search_api_key", name=current.name, secret=api_key
            )
        return self._search_configs.append_version(
            config_id=config_id,
            user_id=user.id,
            name=current.name,
            provider_type=current.provider_type,
            base_url=current.base_url,
            credential_version_id=info.version_id,
        )

    async def test_search_connection(self, user: Any, *, config_id: str) -> ProviderTestResult:
        current = self._search_configs.get_current(user.id, config_id)
        api_key = None
        if current.credential_version_id is not None:
            api_key = self._vault.read_for_execution(
                user_id=user.id, credential_version_id=current.credential_version_id
            )
        provider = build_search_provider(current.provider_type)
        result = await provider.test_connection(api_key=api_key, base_url=current.base_url)
        self._search_configs.mark_connection(
            user.id, config_id, result.status.value.lower(), _now()
        )
        return result

    def delete_search_config(self, user: Any, *, config_id: str) -> None:
        current = self._search_configs.get_current(user.id, config_id)
        if current.credential_version_id is not None:
            self._vault.revoke_by_version(
                user_id=user.id, credential_version_id=current.credential_version_id
            )
        self._search_configs.delete(user.id, config_id)

    # ---- Guards (stable business errors) ----

    def require_available_model_config(self, user: Any) -> ModelConfig:
        config = self._model_configs.get_default(user.id)
        if config is None or config.connection_status != "available":
            raise errors.ModelNotConfiguredError("尚未配置可用的 AI 模型")
        return config

    def require_available_search_config(self, user: Any) -> SearchConfig:
        config = next(
            (
                c
                for c in self._search_configs.list_current(user.id)
                if c.connection_status == "available"
            ),
            None,
        )
        if config is None:
            raise errors.SearchProviderNotConfiguredError("尚未配置可用的搜索服务")
        return config
