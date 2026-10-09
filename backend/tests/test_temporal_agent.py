from __future__ import annotations

import os

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, FunctionToolset, RunContext
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin, PydanticAIWorkflow, TemporalDurability
from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from app.agent.runtime import (
        KAIROS_AGENT_NAME,
        MAX_AGENT_ACTIONABLE_SEARCH_SOURCES,
        _agent_commit_extraction,
        _agent_fetch_source,
        _agent_search_sources,
        _prepare_fetch_url,
        kairos_agent,
    )
    from app.workflows import KairosAgentWorkflow


class _TemporalTestDeps(BaseModel):
    value: str


async def _echo(ctx: RunContext[_TemporalTestDeps], value: str) -> str:
    del ctx
    return f"echo:{value}"


_temporal_test_agent = Agent(
    "test",
    name="kairos-temporal-test-agent",
    deps_type=_TemporalTestDeps,
    toolsets=[FunctionToolset([_echo], id="kairos-temporal-test-toolset")],
    capabilities=[TemporalDurability()],
)


@workflow.defn
class _TemporalTestWorkflow(PydanticAIWorkflow):
    __pydantic_ai_agents__ = [_temporal_test_agent]

    @workflow.run
    async def run(self) -> str:
        result = await _temporal_test_agent.run("call the echo tool", deps=_TemporalTestDeps(value="durable"))
        return result.output


def test_kairos_uses_one_named_pydantic_agent_with_durable_toolset_ids() -> None:
    assert KAIROS_AGENT_NAME == "kairos-agent-v1"
    assert kairos_agent.name == KAIROS_AGENT_NAME
    toolset_ids = {toolset.id for toolset in kairos_agent.toolsets if toolset.id is not None}
    assert "kairos-web-v1" in toolset_ids
    assert "kairos-workspace-v1" in toolset_ids
    assert "kairos-collection-v1" in toolset_ids
    assert "kairos-search-v1" in toolset_ids
    collection_toolset = next(
        toolset for toolset in kairos_agent.toolsets if toolset.id == "kairos-collection-v1"
    )
    assert {tool.name for tool in collection_toolset.tools.values()} == {
        "fetch_source",
        "inspect_snapshot",
        "commit_extraction",
        "get_collection_progress",
    }
    search_toolset = next(toolset for toolset in kairos_agent.toolsets if toolset.id == "kairos-search-v1")
    assert {tool.name for tool in search_toolset.tools.values()} == {"search_sources"}


def test_generic_fetch_url_is_hidden_for_collection_runs() -> None:
    from types import SimpleNamespace

    from pydantic_ai.tools import ToolDefinition

    tool_def = ToolDefinition(name="fetch_url")
    collection_ctx = SimpleNamespace(deps=SimpleNamespace(spec_version_id="spec-1"))
    general_ctx = SimpleNamespace(deps=SimpleNamespace(spec_version_id=None))

    assert _prepare_fetch_url(collection_ctx, tool_def) is None
    assert _prepare_fetch_url(general_ctx, tool_def) == tool_def


@pytest.mark.asyncio
async def test_agent_fetch_source_recovers_from_model_url_guess(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app.domain import CollectionError

    async def reject_url(_ctx: object, _url: str) -> object:
        raise CollectionError("SOURCE_OUT_OF_SCOPE", "URL is not in the current collection spec")

    monkeypatch.setattr("app.agent.runtime.fetch_source", reject_url)
    result = await _agent_fetch_source(SimpleNamespace(), "https://example.com/guess")

    assert result.status.value == "FAILED"
    assert result.failure_code == "SOURCE_OUT_OF_SCOPE"
    assert result.url == "https://example.com/guess"


@pytest.mark.asyncio
async def test_agent_search_sources_bounds_actionable_frontier(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app.domain import SearchSourceResult, SearchSourcesResult

    full_result = SearchSourcesResult(
        search_round_id="round-1",
        query="frameworks",
        returned_results=5,
        new_sources_count=5,
        sources=[
            SearchSourceResult(
                source_id=f"source-{index}",
                url=f"https://example.com/{index}",
                title="",
                snippet="",
                rank=index,
            )
            for index in range(1, 6)
        ],
    )

    async def return_full_result(_ctx: object, _query: str, max_results: int | None = None) -> object:
        return full_result

    monkeypatch.setattr("app.agent.runtime.search_sources", return_full_result)
    result = await _agent_search_sources(SimpleNamespace(), "frameworks")

    assert len(result.sources) == MAX_AGENT_ACTIONABLE_SEARCH_SOURCES == 2
    assert result.returned_results == 5
    assert result.new_sources_count == 5


@pytest.mark.asyncio
async def test_agent_search_sources_recovers_when_round_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.domain import CollectionError

    async def reject_search(_ctx: object, _query: str, max_results: int | None = None) -> object:
        raise CollectionError("SEARCH_ROUND_LIMIT", "search round limit has been reached")

    monkeypatch.setattr("app.agent.runtime.search_sources", reject_search)
    result = await _agent_search_sources(SimpleNamespace(), "frameworks")

    assert result.search_round_id == "search-round-limit"
    assert result.sources == []


@pytest.mark.asyncio
async def test_agent_commit_extraction_recovers_from_duplicate_payload_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.domain import CollectionError, CommitExtractionInput

    async def reject_commit(_ctx: object, _input_data: CommitExtractionInput) -> object:
        raise CollectionError(
            "ALREADY_COMMITTED_DIFFERENT_PAYLOAD",
            "snapshot already has a different extraction payload",
        )

    monkeypatch.setattr("app.agent.runtime.commit_extraction", reject_commit)
    result = await _agent_commit_extraction(
        SimpleNamespace(),
        CommitExtractionInput(snapshot_id="snapshot-1"),
    )

    assert result.extraction_commit_id == "commit-not-created"
    assert result.record_count == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pydantic_ai_temporal_agent_runs_model_tool_observe_loop() -> None:
    if os.getenv("KAIROS_RUN_TEMPORAL_TESTS") != "1":
        pytest.skip("set KAIROS_RUN_TEMPORAL_TESTS=1 to start the real Temporal dev server")
    from temporalio.testing import WorkflowEnvironment

    async with await WorkflowEnvironment.start_local() as environment:
        client: Client = await Client.connect(
            environment.client.service_client.config.target_host,
            plugins=[PydanticAIPlugin()],
        )
        async with Worker(
            client,
            task_queue="kairos-temporal-test-queue",
            workflows=[_TemporalTestWorkflow],
        ):
            result = await client.execute_workflow(
                _TemporalTestWorkflow.run,
                id="kairos-temporal-test-workflow",
                task_queue="kairos-temporal-test-queue",
            )
    assert result == '{"_echo":"echo:a"}'


@pytest.mark.integration
@pytest.mark.asyncio
async def test_kairos_workflow_registers_with_the_real_temporal_worker() -> None:
    if os.getenv("KAIROS_RUN_TEMPORAL_TESTS") != "1":
        pytest.skip("set KAIROS_RUN_TEMPORAL_TESTS=1 to start the real Temporal dev server")
    from temporalio.testing import WorkflowEnvironment

    async with await WorkflowEnvironment.start_local() as environment:
        client: Client = await Client.connect(
            environment.client.service_client.config.target_host,
            plugins=[PydanticAIPlugin()],
        )
        async with Worker(
            client,
            task_queue="kairos-main-agent-registration-queue",
            workflows=[KairosAgentWorkflow],
        ):
            assert KairosAgentWorkflow.__pydantic_ai_agents__ == [kairos_agent]
