"""M-09 Task 5: SourceSearch — candidate-site merge + stable missing-config error."""

from __future__ import annotations

import pytest
from app.activities.execution_seam import ExecutionUnit
from app.auth.models import User
from app.auth.repository import UserRepository
from app.credentials.models import SearchConfig
from app.discovery.source_search import (
    SEARCH_PROVIDER_NOT_CONFIGURED,
    SearchService,
    SourceSearchError,
    merge_into_candidate_sites,
)
from app.domain.models import CollectionSpecVersion, ExecutionPreflightResult, Run
from app.domain.repository import TaskRepository
from app.domain.task_types import TaskType
from app.infra.db import Base
from app.providers.repository import SearchConfigRepository
from app.providers.search_protocol import SearchResult
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


def test_merge_search_results_into_candidate_sites() -> None:
    results = [
        SearchResult(
            url="https://example.com/a", title="A", snippet="s", provider="p", rank=1, query="q"
        ),
        SearchResult(
            url="https://example.com/b", title="B", snippet="s", provider="p", rank=2, query="q"
        ),
        SearchResult(
            url="https://other.com/x", title="X", snippet="s", provider="p", rank=3, query="q"
        ),
    ]
    sites = merge_into_candidate_sites(results)
    hosts = {s.site_host for s in sites}
    assert hosts == {"example.com", "other.com"}
    example = next(s for s in sites if s.site_host == "example.com")
    assert len(example.evidence) == 2  # 每条搜索证据都保留


class _EmptyConfigRepo:
    def list_current(self, user_id: int):
        return []


def test_missing_search_config_is_stable_error() -> None:
    service = SearchService(None, vault=object(), search_configs=_EmptyConfigRepo())
    with pytest.raises(SourceSearchError) as exc:
        service._require_config(1)
    assert exc.value.code == SEARCH_PROVIDER_NOT_CONFIGURED


class _SearchProvider:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.calls: list[dict] = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        return self.results


class _SearchVault:
    def read_for_execution(self, *, user_id: int, credential_version_id: int) -> str:
        return "search-secret"


def _frozen_search_case(
    tmp_path,
    *,
    source_hint: str = "山东省人民政府官网",
    source_hints: list[str] | None = None,
) -> tuple[Session, User, Run, SearchConfig, SearchConfig]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'source-search.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    user = UserRepository(db).create("source@example.com", "hash", None)
    task = TaskRepository(db).create(user_id=user.id, title="source", task_type="hybrid")
    db.add(
        CollectionSpecVersion(
            user_id=user.id,
            task_id=task.id,
            version=1,
            spec_type="collection",
            schema_version="m06.1",
            payload={
                "task_type": TaskType.HYBRID.value,
                "source_scope": {
                    "mode": TaskType.HYBRID.value,
                    "seed_urls": [],
                    "source_hints": [source_hint] if source_hints is None else source_hints,
                    "resolution_scope": "NAMED_SOURCE_ONLY",
                },
            },
        )
    )
    run = Run(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1)
    db.add(run)
    configs = SearchConfigRepository(db)
    v1 = configs.create_version(
        user_id=user.id,
        name="frozen v1",
        provider_type="frozen-provider",
        base_url="https://v1.example.test",
        credential_version_id=101,
    )
    v1.connection_status = "available"
    db.commit()
    v2 = configs.append_version(
        config_id=v1.config_id,
        user_id=user.id,
        name="current v2",
        provider_type="current-provider",
        base_url="https://v2.example.test",
        credential_version_id=202,
    )
    v2.connection_status = "available"
    db.add(
        ExecutionPreflightResult(
            user_id=user.id,
            task_id=task.id,
            spec_version=1,
            plan_version=1,
            capability_manifest_version="task-5-test",
            status="READY",
            issues=[],
            search_config_id=v1.config_id,
            search_config_version=v1.version,
        )
    )
    db.commit()
    return db, user, run, v1, v2


@pytest.mark.asyncio
async def test_source_search_uses_frozen_config_and_filters_named_source(tmp_path) -> None:
    """Would fail if a Run reads the current search config or admits an unrelated host."""
    db, _, run, v1, _ = _frozen_search_case(tmp_path)
    provider = _SearchProvider(
        [
            SearchResult(
                url="https://www.shandong.gov.cn/news",
                title="山东省人民政府 公告",
                snippet="官方发布",
                provider="frozen-provider",
                rank=1,
                query="山东政府",
            ),
            SearchResult(
                url="https://commercial.example.test/ad",
                title="商业推广",
                snippet="山东资讯",
                provider="frozen-provider",
                rank=2,
                query="山东政府",
            ),
        ]
    )

    class Vault:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int]] = []

        def read_for_execution(self, *, user_id: int, credential_version_id: int) -> str:
            self.calls.append((user_id, credential_version_id))
            return "v1-secret"

    vault = Vault()
    provider_types: list[str] = []

    def build_provider(provider_type: str) -> _SearchProvider:
        provider_types.append(provider_type)
        return provider

    service = SearchService(db, vault=vault, provider_builder=build_provider)

    result = await service.execute(
        ExecutionUnit(
            run_id=run.id,
            index=1,
            unit_type="node",
            input_fingerprint="search-test",
            node_id="search-1",
            node_type="source_search",
            parameters={"query": "山东政府", "max_results": 2},
        )
    )

    assert result.status == "OK"
    assert result.committed_refs["candidate_sites"] == 1
    urls = list(select_urls(db, run.task_id))
    assert urls == ["https://www.shandong.gov.cn/news"]
    assert provider_types == [v1.provider_type]
    assert provider.calls[0]["base_url"] == v1.base_url
    assert provider.calls[0]["api_key"] == "v1-secret"
    assert vault.calls == [(run.user_id, v1.credential_version_id)]


@pytest.mark.asyncio
async def test_source_search_missing_frozen_config_never_falls_back_to_current(tmp_path) -> None:
    """Would fail if a missing frozen config version falls back to the current default."""
    db, _, run, v1, _ = _frozen_search_case(tmp_path)
    db.query(SearchConfig).filter(
        SearchConfig.config_id == v1.config_id, SearchConfig.version == v1.version
    ).delete()
    db.commit()
    service = SearchService(db, vault=object(), provider_builder=lambda _: _SearchProvider([]))

    with pytest.raises(SourceSearchError) as exc:
        await service.execute(
            ExecutionUnit(
                run_id=run.id,
                index=1,
                unit_type="node",
                input_fingerprint="search-test",
                node_id="search-1",
                node_type="source_search",
                parameters={"query": "山东政府"},
            )
        )

    assert exc.value.code == "FROZEN_CONFIG_UNAVAILABLE"


@pytest.mark.asyncio
async def test_source_search_missing_ready_preflight_never_falls_back_to_current(tmp_path) -> None:
    """Would fail if a Run without READY preflight silently selects a current config."""
    db, _, run, _, _ = _frozen_search_case(tmp_path)
    db.query(ExecutionPreflightResult).delete()
    db.commit()
    provider = _SearchProvider([])
    service = SearchService(db, vault=_SearchVault(), provider_builder=lambda _: provider)

    with pytest.raises(SourceSearchError) as exc:
        await service.execute(
            ExecutionUnit(
                run_id=run.id,
                index=1,
                unit_type="node",
                input_fingerprint="search-test",
                node_id="search-1",
                node_type="source_search",
                parameters={"query": "山东政府"},
            )
        )

    assert exc.value.code == "FROZEN_CONFIG_UNAVAILABLE"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_source_search_null_frozen_refs_never_falls_back_to_current(tmp_path) -> None:
    """Would fail if a READY preflight with incomplete identity selects a current config."""
    db, _, run, _, _ = _frozen_search_case(tmp_path)
    preflight = db.scalar(select(ExecutionPreflightResult))
    assert preflight is not None
    preflight.search_config_id = None
    preflight.search_config_version = None
    db.commit()
    provider = _SearchProvider([])
    service = SearchService(db, vault=_SearchVault(), provider_builder=lambda _: provider)

    with pytest.raises(SourceSearchError) as exc:
        await service.execute(
            ExecutionUnit(
                run_id=run.id,
                index=1,
                unit_type="node",
                input_fingerprint="search-test",
                node_id="search-1",
                node_type="source_search",
                parameters={"query": "山东政府"},
            )
        )

    assert exc.value.code == "FROZEN_CONFIG_UNAVAILABLE"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_source_search_round2_uses_carried_frozen_config(tmp_path) -> None:
    """Round-2（plan_version=2，replan 后）沿用携带的冻结配置 v1，不切到 default v2。"""
    db, _, run, v1, v2 = _frozen_search_case(tmp_path)
    # 模拟 replan 产物：新 plan_version + 携带冻结配置的 READY preflight
    run.plan_version = 2
    db.add(
        ExecutionPreflightResult(
            user_id=run.user_id,
            task_id=run.task_id,
            spec_version=run.spec_version,
            plan_version=2,
            capability_manifest_version="round2",
            status="READY",
            issues=[],
            search_config_id=v1.config_id,
            search_config_version=v1.version,
        )
    )
    db.commit()

    provider = _SearchProvider(
        [
            SearchResult(
                url="https://www.shandong.gov.cn/news",
                title="山东省人民政府 公告",
                snippet="官方发布",
                provider="frozen-provider",
                rank=1,
                query="山东政府",
            )
        ]
    )
    provider_types: list[str] = []

    def build_provider(provider_type: str) -> _SearchProvider:
        provider_types.append(provider_type)
        return provider

    class Vault:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int]] = []

        def read_for_execution(self, *, user_id: int, credential_version_id: int) -> str:
            self.calls.append((user_id, credential_version_id))
            return "v1-secret"

    vault = Vault()
    service = SearchService(db, vault=vault, provider_builder=build_provider)

    result = await service.execute(
        ExecutionUnit(
            run_id=run.id,
            index=1,
            unit_type="node",
            input_fingerprint="search-test",
            node_id="search-1",
            node_type="source_search",
            parameters={"query": "山东政府", "max_results": 2},
        )
    )

    assert result.status == "OK"
    assert provider_types == [v1.provider_type]
    assert vault.calls == [(run.user_id, v1.credential_version_id)]
    assert v2.provider_type not in provider_types


@pytest.mark.asyncio
async def test_named_source_search_with_no_matching_result_admits_no_frontier_host(
    tmp_path,
) -> None:
    """Would fail if named-source resolution expands to an arbitrary result host."""
    db, _, run, _, _ = _frozen_search_case(tmp_path)
    provider = _SearchProvider(
        [
            SearchResult(
                url="https://commercial.example.test/ad",
                title="商业推广",
                snippet="山东资讯",
                provider="frozen-provider",
                rank=1,
                query="山东政府",
            )
        ]
    )
    service = SearchService(db, vault=_SearchVault(), provider_builder=lambda _: provider)

    result = await service.execute(
        ExecutionUnit(
            run_id=run.id,
            index=1,
            unit_type="node",
            input_fingerprint="search-test",
            node_id="search-1",
            node_type="source_search",
            parameters={"query": "山东政府"},
        )
    )

    assert result.committed_refs["candidate_sites"] == 0
    assert list(select_urls(db, run.task_id)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source_hint", ["官网", "网站", "，。！？"])
async def test_named_source_search_with_empty_normalized_hint_admits_no_candidates(
    tmp_path, source_hint: str
) -> None:
    """Would fail if an empty approved hint admitted every search result."""
    db, _, run, _, _ = _frozen_search_case(tmp_path, source_hint=source_hint)
    provider = _SearchProvider(
        [
            SearchResult(
                url="https://commercial.example.test/ad",
                title="商业推广",
                snippet="无关结果",
                provider="frozen-provider",
                rank=1,
                query="山东政府",
            )
        ]
    )
    service = SearchService(db, vault=_SearchVault(), provider_builder=lambda _: provider)

    result = await service.execute(
        ExecutionUnit(
            run_id=run.id,
            index=1,
            unit_type="node",
            input_fingerprint="search-test",
            node_id="search-1",
            node_type="source_search",
            parameters={"query": "山东政府"},
        )
    )

    assert result.committed_refs["candidate_sites"] == 0
    assert list(select_urls(db, run.task_id)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source_hints", [[], [" ", "\t"]])
async def test_named_source_search_without_usable_hint_admits_no_candidates(
    tmp_path, source_hints: list[str]
) -> None:
    """Would fail if malformed named-source scope were treated as EXPLORATORY."""
    db, _, run, _, _ = _frozen_search_case(tmp_path, source_hints=source_hints)
    provider = _SearchProvider(
        [
            SearchResult(
                url="https://commercial.example.test/ad",
                title="商业推广",
                snippet="无关结果",
                provider="frozen-provider",
                rank=1,
                query="山东政府",
            )
        ]
    )
    service = SearchService(db, vault=_SearchVault(), provider_builder=lambda _: provider)

    result = await service.execute(
        ExecutionUnit(
            run_id=run.id,
            index=1,
            unit_type="node",
            input_fingerprint="search-test",
            node_id="search-1",
            node_type="source_search",
            parameters={"query": "山东政府"},
        )
    )

    assert result.committed_refs["candidate_sites"] == 0
    assert list(select_urls(db, run.task_id)) == []


@pytest.mark.asyncio
async def test_generic_source_type_hint_does_not_filter_search_results(tmp_path) -> None:
    """泛化来源类型（新闻聚合网站/科技新闻媒体）不是具体命名来源：不得过滤真实搜索结果。"""
    db, _, run, _, _ = _frozen_search_case(tmp_path, source_hints=["新闻聚合网站", "科技新闻媒体"])
    provider = _SearchProvider(
        [
            SearchResult(
                url="https://news.example.test/a",
                title="全球AI监管政策观察",
                snippet="各国监管动态",
                provider="frozen-provider",
                rank=1,
                query="人工智能监管政策",
            ),
            SearchResult(
                url="https://policy.example.test/b",
                title="人工智能治理年度报告",
                snippet="政策解读",
                provider="frozen-provider",
                rank=2,
                query="人工智能监管政策",
            ),
        ]
    )
    service = SearchService(db, vault=_SearchVault(), provider_builder=lambda _: provider)

    result = await service.execute(
        ExecutionUnit(
            run_id=run.id,
            index=1,
            unit_type="node",
            input_fingerprint="search-test",
            node_id="search-1",
            node_type="source_search",
            parameters={"query": "人工智能监管政策"},
        )
    )

    assert result.committed_refs["candidate_sites"] == 2
    assert len(list(select_urls(db, run.task_id))) == 2


@pytest.mark.asyncio
async def test_topic_collection_descriptor_hint_does_not_filter(tmp_path) -> None:
    """主题采集描述（人工智能安全相关新闻报道）不是命名来源：不得过滤真实搜索结果。"""
    db, _, run, _, _ = _frozen_search_case(
        tmp_path, source_hints=["人工智能安全相关新闻报道", "搜索引擎搜索结果"]
    )
    provider = _SearchProvider(
        [
            SearchResult(
                url="https://news.example.test/a",
                title="人工智能安全监管动态",
                snippet="最新报道",
                provider="frozen-provider",
                rank=1,
                query="人工智能安全 最新新闻报道",
            ),
        ]
    )
    service = SearchService(db, vault=_SearchVault(), provider_builder=lambda _: provider)

    result = await service.execute(
        ExecutionUnit(
            run_id=run.id,
            index=1,
            unit_type="node",
            input_fingerprint="search-test",
            node_id="search-1",
            node_type="source_search",
            parameters={"query": "人工智能安全 最新新闻报道"},
        )
    )

    assert result.committed_refs["candidate_sites"] == 1
    assert len(list(select_urls(db, run.task_id))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source_hint", ["新华网", "山东省人民政府官网"])
async def test_concrete_named_source_hint_still_filters(tmp_path, source_hint: str) -> None:
    """具体命名来源 hint 仍按冻结来源过滤，只保留命中的结果。"""
    db, _, run, _, _ = _frozen_search_case(tmp_path, source_hint=source_hint)
    provider = _SearchProvider(
        [
            SearchResult(
                url="https://named.example.test/a",
                title=f"{source_hint}发布最新公告",
                snippet="官方信息",
                provider="frozen-provider",
                rank=1,
                query="来源查询",
            ),
            SearchResult(
                url="https://unrelated.example.test/b",
                title="无关商业推广",
                snippet="与来源无关",
                provider="frozen-provider",
                rank=2,
                query="来源查询",
            ),
        ]
    )
    service = SearchService(db, vault=_SearchVault(), provider_builder=lambda _: provider)

    result = await service.execute(
        ExecutionUnit(
            run_id=run.id,
            index=1,
            unit_type="node",
            input_fingerprint="search-test",
            node_id="search-1",
            node_type="source_search",
            parameters={"query": "来源查询"},
        )
    )

    # 只保留命中了具体来源标题的结果
    assert result.committed_refs["candidate_sites"] == 1
    urls = select_urls(db, run.task_id)
    assert list(urls) == ["https://named.example.test/a"]


def select_urls(db: Session, task_id: int):
    from app.domain.models import URLResource

    return db.scalars(select(URLResource.url).where(URLResource.task_id == task_id))
