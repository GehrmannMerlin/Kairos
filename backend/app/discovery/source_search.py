"""SourceSearch executor（M-09 / D-069）。

消费 M-08 validated Plan 中 SourceSearch 节点的 typed 参数（query/max_results/
locale），复用 M-03 SearchProvider + SearchConfig + CredentialVault。不调用 LLM
生成 query（PlanGenerator 负责计划层参数）；只执行已验证参数。搜索结果合并为
Candidate Sites（保留 query/provider/rank/result URL 证据）并写入 Frontier。

搜索配置缺失语义：计划含 SourceSearch → 任务确实需要搜索（EXPLORATORY/HYBRID）。
缺可用 SearchConfig → 稳定 SEARCH_PROVIDER_NOT_CONFIGURED；任务/计划不丢失，
不静默替换为别的 Provider。SPECIFIED_SOURCE 计划不含 SourceSearch，天然可继续。
"""

from __future__ import annotations

from app.activities.execution_seam import ExecuteUnitResult, ExecutionUnit
from app.discovery.errors import DiscoveryError
from app.discovery.frontier import UrlFrontierRepository
from app.discovery.models import (
    CandidateSite,
    DiscoveryEvidence,
    DiscoverySource,
    SearchResultRef,
)
from app.providers.search_protocol import SearchResult
from app.reliability.provider_limit import ProviderLimiter

SEARCH_PROVIDER_NOT_CONFIGURED = "SEARCH_PROVIDER_NOT_CONFIGURED"

# M-16 进程内 provider 限流缓存（key = 安全 metadata hash，非明文 Key）
_SEARCH_LIMITERS: dict[str, ProviderLimiter] = {}


class SourceSearchError(DiscoveryError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def merge_into_candidate_sites(results: list[SearchResult]) -> list[CandidateSite]:
    """同一网站多条搜索结果合并为 site candidate，保留每条来源证据。"""
    from urllib.parse import urlsplit

    by_host: dict[str, CandidateSite] = {}
    for r in results:
        host = (urlsplit(r.url).hostname or "").lower()
        if not host:
            continue
        site = by_host.get(host)
        if site is None:
            site = CandidateSite(site_host=host, display_url=r.url, evidence=[], depth=0)
            by_host[host] = site
        site.evidence.append(
            SearchResultRef(
                url=r.url,
                title=r.title,
                snippet=r.snippet,
                provider=r.provider,
                rank=r.rank,
                query=r.query,
            )
        )
    return list(by_host.values())


class SearchService:
    def __init__(self, db, *, vault=None, search_configs=None, provider_builder=None,
                 retry_base_delay_seconds: float = 2.0) -> None:
        self._db = db
        self._vault = vault
        self._search_configs = search_configs
        self._provider_builder = provider_builder
        self._retry_base_delay = retry_base_delay_seconds

    # ---- 依赖解析（生产从 settings 构建；测试可注入） ----

    def _vault_instance(self):
        if self._vault is not None:
            return self._vault
        from app.config import get_settings
        from app.credentials import crypto
        from app.credentials.repository import CredentialRepository
        from app.credentials.vault import CredentialVault

        settings = get_settings()
        return CredentialVault(
            master_key=crypto.master_key_from_env_value(settings.credential_master_key),
            key_version=settings.credential_key_version,
            repository=CredentialRepository(self._db),
        )

    def _available_search_config(self, user_id: int):
        from app.providers.repository import SearchConfigRepository

        repo = self._search_configs or SearchConfigRepository(self._db)
        for cfg in repo.list_current(user_id):
            if cfg.connection_status == "available":
                return cfg
        return None

    def _require_config(self, user_id: int):
        cfg = self._available_search_config(user_id)
        if cfg is None:
            raise SourceSearchError(SEARCH_PROVIDER_NOT_CONFIGURED, "尚未配置可用的搜索服务")
        return cfg

    def _build_provider(self, provider_type: str):
        if self._provider_builder is not None:
            return self._provider_builder(provider_type)
        from app.providers.registry import build_search_provider

        return build_search_provider(provider_type)

    # ---- 执行 ----

    async def execute(self, unit: ExecutionUnit) -> ExecuteUnitResult:
        from app.domain.models import Run

        run = self._db.get(Run, unit.run_id)
        if run is None:
            return ExecuteUnitResult(
                unit_index=unit.index,
                status="FAILED",
                error_code="RUN_NOT_FOUND",
                committed_refs={},
            )
        cfg = self._require_config(run.user_id)
        vault = self._vault_instance()
        api_key = (
            vault.read_for_execution(
                user_id=run.user_id, credential_version_id=cfg.credential_version_id
            )
            if cfg.credential_version_id is not None
            else None
        )
        provider = self._build_provider(cfg.provider_type)
        params = unit.parameters or {}
        query = str(params.get("query") or "")
        limit = int(params.get("max_results") or 20)
        # M-16 Provider 限流 + 有界重试（429/bounded backoff+jitter；auth/quota 不重试）
        from app.config import get_settings
        from app.reliability.capacity import capacity_from_settings
        from app.reliability.errors import classify_provider_error
        from app.reliability.provider_limit import (
            ThrottleKey,
            call_with_provider_retry,
        )

        cap = capacity_from_settings(get_settings())
        key = ThrottleKey(
            family=cfg.provider_type, config_id=cfg.credential_version_id or 0,
            user_id=run.user_id,
        )
        limiter = _SEARCH_LIMITERS.setdefault(
            key.fingerprint(),
            ProviderLimiter(
                min_interval_seconds=cap.provider_throttle_min_interval_seconds,
                max_burst=cap.provider_throttle_max_burst,
                key=key.fingerprint(),
            ),
        )
        results = await call_with_provider_retry(
            limiter=limiter,
            fn=lambda: provider.search(
                query=query, limit=limit, api_key=api_key, base_url=cfg.base_url
            ),
            max_attempts=cap.default_retry_max_attempts,
            error_class_fn=classify_provider_error,
            base_delay_seconds=self._retry_base_delay,
        )
        sites = merge_into_candidate_sites(results)
        frontier = UrlFrontierRepository(self._db)
        hashes: list[str] = []
        for site in sites:
            for ref in site.evidence:
                h, _ = frontier.upsert_discovery(
                    task_id=run.task_id,
                    user_id=run.user_id,
                    run_id=run.id,
                    spec_version=run.spec_version,
                    raw_url=ref.url,
                    source=DiscoverySource.SEARCH_RESULT,
                    evidence=DiscoveryEvidence(
                        source=DiscoverySource.SEARCH_RESULT,
                        query=query,
                        provider=ref.provider,
                        rank=ref.rank,
                        result_url=ref.url,
                    ),
                )
                hashes.append(h)
        # SSE 聚合事件（M-07 SSETaskEvent 复用）：只发用户重要发现事件，不逐 URL
        from app.state.events import append_domain_event

        append_domain_event(
            self._db,
            user_id=run.user_id,
            aggregate_type="task",
            aggregate_id=run.task_id,
            event_type="discovery.candidates_found",
            aggregate_version=1,
            payload={
                "candidate_sites": len(sites),
                "candidates": len(hashes),
                "query": query,
                "provider": cfg.provider_type,
            },
            actor_type="system",
            run_id=run.id,
            node_run_id=None,
        )
        self._db.commit()
        return ExecuteUnitResult(
            unit_index=unit.index,
            status="OK",
            committed_refs={
                "run_id": run.id,
                "node_id": unit.node_id,
                "node_type": unit.node_type,
                "task_id": run.task_id,
                "candidate_sites": len(sites),
                "candidates": len(hashes),
            },
        )
