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
    from app.agent.runtime import KAIROS_AGENT_NAME, kairos_agent
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
