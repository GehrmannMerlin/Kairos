from __future__ import annotations

from uuid import uuid4

import pytest
from app.agent.deps import KairosAgentDeps
from app.agent.tools import search_sources
from app.domain import (
    CollectionError,
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionMode,
    CollectionSourceStatus,
    CollectionSpecConfirm,
)
from app.repositories import (
    confirm_collection_spec,
    create_task_run,
    get_collection_spec,
    insert_task,
    list_collection_sources,
    list_search_rounds,
    set_collection_source_status,
)
from app.search import NormalizedSearchResult, SearchProviderResponse
from app.url_policy import canonicalize_url
from pydantic_ai import RunContext


class FakeSearchProvider:
    provider_name = "fake"

    def __init__(self, results: list[NormalizedSearchResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, int]] = []

    async def search(self, *, query: str, max_results: int) -> SearchProviderResponse:
        self.calls.append((query, max_results))
        return SearchProviderResponse(
            provider=self.provider_name, query=query, results=self.results[:max_results]
        )


def _context(owner_id: str, task_id: str, run_id: str, spec_id: str) -> RunContext[KairosAgentDeps]:
    return RunContext(
        deps=KairosAgentDeps(
            user_id=owner_id,
            task_id=task_id,
            task_run_id=run_id,
            spec_version_id=spec_id,
            model_config_id="test-model",
        ),
        model=None,
        usage=None,
        run_id=run_id,
    )


async def _exploratory_scope(
    mode: CollectionMode = CollectionMode.EXPLORATORY,
    *,
    seed_urls: list[str] | None = None,
    scope_domains: list[str] | None = None,
) -> tuple[str, str, str, str]:
    owner_id = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    await insert_task(task_id, owner_id)
    await confirm_collection_spec(
        task_id,
        owner_id,
        CollectionSpecConfirm(
            goal="Find projects",
            fields=[CollectionFieldSpec(name="name", type=CollectionFieldType.STRING, required=True)],
            seed_urls=seed_urls or [],
            target_count=2,
            mode=mode,
            scope_domains=scope_domains or [],
        ),
    )
    spec = await get_collection_spec(task_id, owner_id)
    assert spec is not None
    await create_task_run(task_id, owner_id, run_id, f"workflow-{uuid4().hex}", "find projects")
    return owner_id, task_id, run_id, spec.spec_version_id


@pytest.mark.asyncio
async def test_search_sources_deduplicates_urls_and_persists_bounded_search_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id, task_id, run_id, spec_id = await _exploratory_scope()
    provider = FakeSearchProvider(
        [
            NormalizedSearchResult(
                url="https://Example.com/framework#one",
                title="First",
                snippet="a" * 500,
                rank=1,
            ),
            NormalizedSearchResult(
                url="https://example.com/framework#two",
                title="Duplicate",
                snippet="duplicate",
                rank=2,
            ),
            NormalizedSearchResult(
                url="http://127.0.0.1/private",
                title="Private",
                snippet="private",
                rank=3,
            ),
        ]
    )
    monkeypatch.setattr("app.agent.tools.resolve_search_provider", lambda config_id=None: provider)

    def safe_validate(url: str) -> str:
        if "127.0.0.1" in url:
            raise CollectionError("PRIVATE_ADDRESS_BLOCKED", "source address is not public")
        return canonicalize_url(url)

    monkeypatch.setattr("app.agent.tools.validate_public_http_url", safe_validate)

    result = await search_sources(_context(owner_id, task_id, run_id, spec_id), "  python   frameworks ")
    sources = await list_collection_sources(task_id, owner_id, spec_id)
    rounds = await list_search_rounds(task_id, run_id, owner_id, spec_id)

    assert result.returned_results == 3
    assert result.new_sources_count == 1
    assert len(result.sources) == 1
    assert result.sources[0].snippet == "a" * 500
    assert len(sources) == 1
    assert sources[0].origin == "SEARCH"
    assert sources[0].canonical_url == "https://example.com/framework"
    assert sources[0].search_snippet == "a" * 500
    assert rounds[0].returned_results == 3
    assert rounds[0].accepted_results == 1
    assert rounds[0].new_sources == 1


@pytest.mark.asyncio
async def test_search_sources_reuses_completed_query_and_deduplicates_across_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id, task_id, run_id, spec_id = await _exploratory_scope()
    provider = FakeSearchProvider(
        [
            NormalizedSearchResult(
                url="https://example.com/framework",
                title="Framework",
                snippet="framework",
                rank=1,
            )
        ]
    )
    monkeypatch.setattr("app.agent.tools.resolve_search_provider", lambda config_id=None: provider)
    monkeypatch.setattr("app.agent.tools.validate_public_http_url", canonicalize_url)
    context = _context(owner_id, task_id, run_id, spec_id)

    first = await search_sources(context, "python", max_results=3)
    source = (await list_collection_sources(task_id, owner_id, spec_id))[0]
    await set_collection_source_status(
        source.source_id, task_id, run_id, owner_id, CollectionSourceStatus.PROCESSED
    )
    same_query = await search_sources(context, " python ", max_results=3)
    second_query = await search_sources(context, "python framework", max_results=3)
    sources = await list_collection_sources(task_id, owner_id, spec_id)
    rounds = await list_search_rounds(task_id, run_id, owner_id, spec_id)

    assert first.new_sources_count == 1
    assert same_query.search_round_id == first.search_round_id
    assert same_query.sources[0].title == "Framework"
    assert second_query.new_sources_count == 0
    assert len(provider.calls) == 2
    assert len(sources) == 1
    assert len(rounds) == 2


@pytest.mark.asyncio
async def test_hybrid_seed_only_scope_is_derived_from_seed_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id, task_id, run_id, spec_id = await _exploratory_scope(
        CollectionMode.HYBRID,
        seed_urls=["https://example.com/seed"],
    )
    provider = FakeSearchProvider(
        [
            NormalizedSearchResult(
                url="https://docs.example.com/ok",
                title="Allowed",
                snippet="allowed",
                rank=1,
            ),
            NormalizedSearchResult(
                url="https://other.example.net/no",
                title="Outside",
                snippet="outside",
                rank=2,
            ),
        ]
    )
    monkeypatch.setattr("app.agent.tools.resolve_search_provider", lambda config_id=None: provider)
    monkeypatch.setattr("app.agent.tools.validate_public_http_url", canonicalize_url)

    result = await search_sources(_context(owner_id, task_id, run_id, spec_id), "projects")
    sources = await list_collection_sources(task_id, owner_id, spec_id)

    assert result.new_sources_count == 1
    assert [source.canonical_url for source in sources] == [
        "https://example.com/seed",
        "https://docs.example.com/ok",
    ]


@pytest.mark.asyncio
async def test_hybrid_search_sources_accepts_only_hostname_boundary_in_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id, task_id, run_id, spec_id = await _exploratory_scope(
        CollectionMode.HYBRID,
        scope_domains=["Example.com"],
    )
    provider = FakeSearchProvider(
        [
            NormalizedSearchResult(
                url="https://docs.example.com/ok",
                title="Allowed",
                snippet="allowed",
                rank=1,
            ),
            NormalizedSearchResult(
                url="https://example.com.attacker.com/bad",
                title="Attacker",
                snippet="bad",
                rank=2,
            ),
        ]
    )
    monkeypatch.setattr("app.agent.tools.resolve_search_provider", lambda config_id=None: provider)
    monkeypatch.setattr("app.agent.tools.validate_public_http_url", canonicalize_url)

    result = await search_sources(_context(owner_id, task_id, run_id, spec_id), "projects")
    sources = await list_collection_sources(task_id, owner_id, spec_id)

    assert result.new_sources_count == 1
    assert [source.canonical_url for source in sources] == ["https://docs.example.com/ok"]
