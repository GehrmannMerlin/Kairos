"""M-11 executor 注册（M-08 NODE_EXECUTORS 绑定）：EXTRACT + NORMALIZE。

只注册两个 Plan Node；真实能力不是 fixture。LLM fallback 通过 ExtractionModelResolver
从冻结 PlanVersion 解析用户自己的 ModelConfig + CredentialVault（D-029）。
"""

from __future__ import annotations

from app.plan.executors import register_node_executor
from app.plan.nodes import NodeType


def install_extraction_executors() -> None:
    from app.extraction.executor import ExtractNodeExecutor, NormalizeNodeExecutor
    from app.extraction.model_resolver import ExtractionModelResolver
    from app.infra.deps import get_object_storage, get_session_factory

    def _build_model_resolver(session):
        from app.config import get_settings
        from app.credentials import crypto
        from app.credentials.repository import CredentialRepository
        from app.credentials.vault import CredentialVault
        from app.providers.repository import ModelConfigRepository, SearchConfigRepository
        from app.providers.service import ProviderService

        settings = get_settings()
        vault = CredentialVault(
            master_key=crypto.master_key_from_env_value(settings.credential_master_key),
            key_version=settings.credential_key_version,
            repository=CredentialRepository(session),
        )
        provider_service = ProviderService(
            vault=vault,
            model_configs=ModelConfigRepository(session),
            search_configs=SearchConfigRepository(session),
        )
        return ExtractionModelResolver(session, provider_service=provider_service, vault=vault)

    async def _extract(unit):
        session = get_session_factory()()
        try:
            return await ExtractNodeExecutor(
                session,
                storage=get_object_storage(),
                model_resolver=_build_model_resolver(session),
            ).execute(unit)
        finally:
            session.close()

    async def _normalize(unit):
        session = get_session_factory()()
        try:
            return await NormalizeNodeExecutor(session).execute(unit)
        finally:
            session.close()

    register_node_executor(NodeType.EXTRACT, _extract)
    register_node_executor(NodeType.NORMALIZE, _normalize)
