"""真实 M-10 executor 注册（M-08 NODE_EXECUTORS 绑定）。

Worker 启动时调用 ``install_fetch_executors()``。只注册 FETCH / BROWSER_RENDER
两个 Plan Node（D-008：Agent Plan 不看到 HttpFetch/ScrapyFetch/PlaywrightFetch）。
HTTP / Scrapy 由 Fetch 执行策略确定；Playwright 只在证据驱动时由 BROWSER_RENDER 运行。
Production 与测试 worker 都启用（真实能力，不是 fixture）。
"""

from __future__ import annotations

from app.plan.executors import register_node_executor
from app.plan.nodes import NodeType


def install_fetch_executors(*, allow_hosts: frozenset[str] = frozenset()) -> None:
    from app.crawling.browser import BrowserRenderNodeExecutor
    from app.crawling.credentials import WebsiteCredentialService
    from app.crawling.fetch_executor import FetchNodeExecutor
    from app.crawling.site_strategy import SiteStrategyService
    from app.infra.deps import get_object_storage, get_session_factory

    def _build_vault(session):
        from app.config import get_settings
        from app.credentials import crypto
        from app.credentials.repository import CredentialRepository
        from app.credentials.vault import CredentialVault

        settings = get_settings()
        return CredentialVault(
            master_key=crypto.master_key_from_env_value(settings.credential_master_key),
            key_version=settings.credential_key_version,
            repository=CredentialRepository(session),
        )

    async def _fetch(unit):
        session = get_session_factory()()
        try:
            from app.config import get_settings

            settings = get_settings()
            vault = _build_vault(session)
            resolver = WebsiteCredentialService(session, vault)
            strategy = SiteStrategyService(
                session, ttl_seconds=settings.site_strategy_ttl_seconds
            )
            return await FetchNodeExecutor(
                session,
                storage=get_object_storage(),
                site_strategy=strategy,
                credential_resolver=resolver,
                allow_hosts=allow_hosts,
                max_internal_retries=settings.fetch_internal_retries,
                retry_base_seconds=settings.fetch_internal_retry_base_seconds,
            ).execute(unit)
        finally:
            session.close()

    async def _browser_render(unit):
        session = get_session_factory()()
        try:
            from app.config import get_settings

            settings = get_settings()
            strategy = SiteStrategyService(
                session, ttl_seconds=settings.site_strategy_ttl_seconds
            )
            return await BrowserRenderNodeExecutor(
                session,
                storage=get_object_storage(),
                site_strategy=strategy,
                allow_hosts=allow_hosts,
            ).execute(unit)
        finally:
            session.close()

    register_node_executor(NodeType.FETCH, _fetch)
    register_node_executor(NodeType.BROWSER_RENDER, _browser_render)
