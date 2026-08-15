"""ProviderService: model/search config lifecycle, connection tests, guards.

Routes stay thin; all credential decryption happens here through
``CredentialVault.read_for_execution`` (controlled execution path only).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from app.credentials.models import ModelConfig, SearchConfig
from app.credentials.vault import CredentialVault
from app.providers import errors
from app.providers.fingerprint import fingerprint_api_key
from app.providers.protocol import (
    DetectionConfidence,
    ModelCatalogResult,
    ModelProbeResult,
    ProviderTestResult,
    ProviderTestStatus,
    SearchProbeResult,
)
from app.providers.registry import (
    build_model_provider,
    build_search_provider,
    get_model_definition,
    get_search_definition,
    validate_model_provider_type,
    validate_search_provider_type,
)
from app.providers.repository import ModelConfigRepository, SearchConfigRepository
from app.providers.transport import HttpClient


def _now() -> datetime:
    return datetime.now(UTC)


def _is_valid_http_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


# Safe, stable probe messages — never the provider's raw response.
_PROBE_MESSAGES: dict[ProviderTestStatus, str] = {
    ProviderTestStatus.AVAILABLE: "连接成功",
    ProviderTestStatus.AUTH_FAILED: "API Key 无效",
    ProviderTestStatus.MODEL_NOT_FOUND: "模型不存在",
    ProviderTestStatus.RATE_LIMITED: "服务商返回限流，请稍后重试",
    ProviderTestStatus.NETWORK_ERROR: "无法连接服务商",
    ProviderTestStatus.FAILED: "连接失败",
}


class ProviderService:
    def __init__(
        self,
        *,
        vault: CredentialVault,
        model_configs: ModelConfigRepository,
        search_configs: SearchConfigRepository,
        http: HttpClient | None = None,
    ) -> None:
        self._vault = vault
        self._model_configs = model_configs
        self._search_configs = search_configs
        self._http = http

    # ---- Model config lifecycle ----

    def list_model_configs(self, user: Any) -> list[ModelConfig]:
        return self._model_configs.list_current(user.id)

    def has_available_search_config(self, user: Any) -> bool:
        return any(
            config.connection_status == "available"
            for config in self._search_configs.list_current(user.id)
        )

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
        definition = get_model_definition(provider_type)
        if definition.requires_base_url and not _is_valid_http_url(base_url or ""):
            raise errors.ProviderValidationError(f"{definition.display_name} 需要合法的 Base URL")
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
        definition = get_model_definition(provider_type)
        if definition.requires_base_url and not _is_valid_http_url(base_url or ""):
            raise errors.ProviderValidationError(f"{definition.display_name} 需要合法的 Base URL")
        current = self._model_configs.get_current(user.id, config_id)
        if current.provider_type != provider_type:
            raise errors.ProviderValidationError(
                "已有凭证不能切换 Provider；请为新的 Provider 新建模型配置"
            )
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
        provider = build_model_provider(current.provider_type, http=self._http)
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

    async def list_available_models(
        self,
        user: Any,
        *,
        provider_type: str,
        api_key: str | None,
        base_url: str | None,
        config_id: str | None,
    ) -> ModelCatalogResult:
        """Load a real catalog using a transient key or one owned config credential."""
        validate_model_provider_type(provider_type)
        if api_key and config_id:
            raise errors.ProviderValidationError("模型目录只能使用一种凭证来源")

        current = None
        if config_id:
            current = self._model_configs.get_current(user.id, config_id)
            if current.provider_type != provider_type:
                raise errors.ProviderValidationError(
                    "已有凭证不能用于其他 Provider；请新建模型配置"
                )
            if current.credential_version_id is not None:
                api_key = self._vault.read_for_execution(
                    user_id=user.id,
                    credential_version_id=current.credential_version_id,
                )

        definition = get_model_definition(provider_type)
        resolved = base_url.strip() if base_url else None
        if definition.requires_base_url:
            if not resolved and current is not None and current.provider_type == provider_type:
                resolved = current.base_url
            if not _is_valid_http_url(resolved or ""):
                raise errors.ProviderValidationError(
                    f"{definition.display_name} 需要合法的 Base URL"
                )
        else:
            resolved = definition.default_base_url

        if definition.requires_api_key and not api_key:
            raise errors.ProviderValidationError(f"{definition.display_name} 需要 API Key")

        provider = build_model_provider(provider_type, http=self._http)
        return await provider.list_models(api_key=api_key, base_url=resolved)

    # ---- Model probe (unsaved config; never persists the key) ----

    async def probe_model(
        self,
        *,
        api_key: str | None,
        provider_type: str | None,
        base_url: str | None,
        model_name: str | None,
    ) -> ModelProbeResult:
        """Probe an unsaved key against at most ONE provider (D-073).

        Stage 1 fingerprints the key locally (never sends it). Only a single
        high-confidence match — or an explicit user-selected provider — triggers
        stage 2, a single real request to that provider. The key is never stored,
        logged, or returned.
        """
        target: str | None
        if provider_type:
            validate_model_provider_type(provider_type)
            target = provider_type
            confidence = DetectionConfidence.HIGH
            probe_method = "manual"
        else:
            fingerprint = fingerprint_api_key(api_key or "")
            if fingerprint.confidence is DetectionConfidence.HIGH:
                target = fingerprint.provider_type
                confidence = DetectionConfidence.HIGH
                probe_method = "fingerprint"
            else:
                # AMBIGUOUS / NONE: never send the key anywhere.
                message = (
                    "无法仅根据 API Key 唯一识别服务商，请选择 Provider 后重新测试"
                    if fingerprint.confidence is DetectionConfidence.AMBIGUOUS
                    else "无法根据 API Key 识别服务商，请选择 Provider"
                )
                return ModelProbeResult(
                    status=None,
                    detection_confidence=fingerprint.confidence,
                    detected_provider=None,
                    candidates=fingerprint.candidates,
                    message=message,
                )

        assert target is not None
        definition = get_model_definition(target)

        resolved = base_url.strip() if base_url else None
        if definition.requires_base_url:
            if not resolved:
                return ModelProbeResult(
                    status=None,
                    detection_confidence=confidence,
                    detected_provider=target,
                    error_code="BASE_URL_REQUIRED",
                    message=f"{definition.display_name} 需要填写 Base URL",
                    probe_method=probe_method,
                )
            if not _is_valid_http_url(resolved):
                return ModelProbeResult(
                    status=None,
                    detection_confidence=confidence,
                    detected_provider=target,
                    error_code="INVALID_BASE_URL",
                    message="Base URL 无效",
                    probe_method=probe_method,
                )
        else:
            resolved = definition.default_base_url

        if definition.requires_api_key and not api_key:
            return ModelProbeResult(
                status=None,
                detection_confidence=confidence,
                detected_provider=target,
                resolved_base_url=resolved,
                error_code="API_KEY_REQUIRED",
                message=f"{definition.display_name} 需要 API Key",
                probe_method=probe_method,
            )

        provider = build_model_provider(target, http=self._http)
        result = await provider.test_connection(
            api_key=api_key, model=model_name, base_url=resolved
        )
        return ModelProbeResult(
            status=result.status,
            detection_confidence=confidence,
            detected_provider=target,
            resolved_base_url=resolved,
            latency_ms=result.latency_ms,
            error_code=result.error_code,
            message=_PROBE_MESSAGES.get(result.status),
            probe_method=probe_method,
        )

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
        definition = get_search_definition(provider_type)
        if definition.requires_base_url and not _is_valid_http_url(base_url or ""):
            raise errors.ProviderValidationError(f"{definition.display_name} 需要合法的 Base URL")
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
        definition = get_search_definition(provider_type)
        if definition.requires_base_url and not _is_valid_http_url(base_url or ""):
            raise errors.ProviderValidationError(f"{definition.display_name} 需要合法的 Base URL")
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

    # ---- Search probe (unsaved config; never persists the key) ----

    async def probe_search(
        self,
        *,
        provider_type: str,
        api_key: str | None,
        base_url: str | None,
    ) -> SearchProbeResult:
        """Probe an unsaved search key (D-074).

        The user always selects the Search Provider explicitly, so no fingerprint
        stage exists — a single real minimal request is made against the resolved
        endpoint. The key is used in the request, then discarded: never stored,
        logged, or returned. ``status`` is ``None`` only for validation stops.
        """
        validate_search_provider_type(provider_type)
        definition = get_search_definition(provider_type)

        resolved = base_url.strip() if base_url else None
        if definition.requires_base_url:
            if not resolved:
                return SearchProbeResult(
                    status=None,
                    provider_type=provider_type,
                    error_code="BASE_URL_REQUIRED",
                    message=f"{definition.display_name} 需要填写 Base URL",
                )
            if not _is_valid_http_url(resolved):
                return SearchProbeResult(
                    status=None,
                    provider_type=provider_type,
                    error_code="INVALID_BASE_URL",
                    message="Base URL 无效",
                )
        else:
            resolved = definition.default_base_url

        if definition.requires_api_key and not api_key:
            return SearchProbeResult(
                status=None,
                provider_type=provider_type,
                resolved_base_url=resolved,
                error_code="API_KEY_REQUIRED",
                message=f"{definition.display_name} 需要 API Key",
            )

        provider = build_search_provider(provider_type, http=self._http)
        result = await provider.test_connection(api_key=api_key, base_url=resolved)
        return SearchProbeResult(
            status=result.status,
            provider_type=provider_type,
            resolved_base_url=resolved,
            latency_ms=result.latency_ms,
            error_code=result.error_code,
            message=_PROBE_MESSAGES.get(result.status),
        )

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
