"""ExtractionModelResolver — 从冻结 PlanVersion 解析 LLM fallback 模型（D-029）。

只把已解密的 api_key 在执行期传给 agent；绝不被日志/Evidence/DomainEvent 捕获（十七）。
"""

from __future__ import annotations

from typing import Any

from app.domain.models import Run
from app.providers.protocol import ResolvedModel


class ExtractionModelResolver:
    def __init__(self, db: Any, *, provider_service: Any = None, vault: Any = None) -> None:
        self._db = db
        self._provider_service = provider_service
        self._vault = vault

    def resolve_for_run(self, run: Run) -> tuple[ResolvedModel | None, str | None, dict]:
        """Return (resolved_model, api_key, audit_metadata) for the run's frozen plan."""
        if self._provider_service is None or self._vault is None:
            return None, None, {}
        from app.auth.models import User
        from app.domain.repository import PlanVersionRepository

        plan = PlanVersionRepository(self._db).get_version(
            run.user_id, run.task_id, run.plan_version
        )
        # persist_plan 把 model_config_id/version 存为 plan_versions 的**列**，graph 放
        # payload；必须读列，payload 恒为 None（DEPLOY-GATE-3 上海政府真实链根因）。
        config_id = plan.model_config_id if plan is not None else None
        config_version = plan.model_config_version if plan is not None else None
        # ProviderService 方法按 User 对象调用（内部用 user.id），不能传 run.user_id int。
        owner = self._db.get(User, run.user_id)
        if owner is None:
            return None, None, {}
        try:
            if config_id and config_version is not None:
                config = self._provider_service.get_model_config_version(
                    owner, config_id=config_id, version=config_version
                )
            else:
                config = self._provider_service.require_available_model_config(
                    owner
                )  # owner-safe default
        except Exception:
            return None, None, {}
        from app.providers.registry import build_model_provider

        provider = build_model_provider(config.provider_type)
        resolved = provider.resolve_model(
            model=config.model_name,
            base_url=config.base_url,
            credential_version_id=config.credential_version_id,
        )
        api_key = None
        if config.credential_version_id is not None:
            api_key = self._vault.read_for_execution(
                user_id=run.user_id, credential_version_id=config.credential_version_id
            )
        audit = {
            "model_config_id": config.config_id,
            "model_config_version": config.version,
            "provider": config.provider_type,
            "model": config.model_name,
        }
        return resolved, api_key, audit
