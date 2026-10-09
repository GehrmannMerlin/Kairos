from __future__ import annotations

from datetime import timedelta

from pydantic_ai import Agent, FunctionToolset, RunContext, Tool
from pydantic_ai.durable_exec.temporal import TemporalDurability
from pydantic_ai.tools import ToolDefinition
from temporalio.common import RetryPolicy

from app.agent.browser_tool import BROWSER_TOOL_ACTIVITY_CONFIG, browser_toolset
from app.agent.deps import KairosAgentDeps
from app.agent.events import agent_event_stream_handler
from app.agent.tools import (
    COLLECTION_TOOL_ACTIVITY_CONFIG,
    WEB_TOOL_ACTIVITY_CONFIG,
    commit_extraction,
    fetch_source,
    fetch_url,
    get_collection_progress,
    inspect_snapshot,
    search_sources,
)
from app.config import get_settings
from app.domain import (
    CollectionError,
    CollectionSourceStatus,
    CommitExtractionInput,
    CommitExtractionResult,
    FetchSourceResult,
    SearchSourcesResult,
)
from app.provider import model_resolver_capability
from app.workspace import workspace_dynamic_toolset

KAIROS_AGENT_NAME = "kairos-agent-v1"
MAX_AGENT_ACTIONABLE_SEARCH_SOURCES = 2

_settings = get_settings()


def _prepare_fetch_url(ctx: RunContext[KairosAgentDeps], tool_def: ToolDefinition) -> ToolDefinition | None:
    """Hide generic web fetches once a collection scope has been confirmed."""
    return None if ctx.deps.spec_version_id is not None else tool_def


async def _agent_fetch_source(ctx: RunContext[KairosAgentDeps], url: str) -> FetchSourceResult:
    """Turn an exploratory model's stale URL guess into a recoverable tool result."""
    try:
        return await fetch_source(ctx, url)
    except CollectionError as exc:
        if exc.code != "SOURCE_OUT_OF_SCOPE":
            raise
        return FetchSourceResult(
            source_id="out-of-scope",
            url=url,
            status=CollectionSourceStatus.FAILED,
            failure_code=exc.code,
            failure_message=exc.message,
        )


async def _agent_search_sources(ctx: RunContext[KairosAgentDeps], query: str, max_results: int | None = None):
    """Keep the durable model context bounded while persisting the full discovery round."""
    try:
        result = await search_sources(ctx, query, max_results=max_results)
    except CollectionError as exc:
        if exc.code != "SEARCH_ROUND_LIMIT":
            raise
        return SearchSourcesResult(
            search_round_id="search-round-limit",
            query=query,
            returned_results=0,
            new_sources_count=0,
            sources=[],
        )
    return result.model_copy(update={"sources": result.sources[:MAX_AGENT_ACTIONABLE_SEARCH_SOURCES]})


async def _agent_commit_extraction(
    ctx: RunContext[KairosAgentDeps], input_data: CommitExtractionInput
) -> CommitExtractionResult:
    """Make duplicate model submissions recoverable without weakening commit validation."""
    try:
        return await commit_extraction(ctx, input_data)
    except CollectionError as exc:
        if exc.code not in {"ALREADY_COMMITTED_DIFFERENT_PAYLOAD", "SNAPSHOT_NOT_FOUND"}:
            raise
        return CommitExtractionResult(
            extraction_commit_id="commit-not-created",
            snapshot_id=input_data.snapshot_id,
            record_ids=[],
            record_count=0,
            idempotent=False,
        )


_web_toolset = FunctionToolset(
    [Tool(fetch_url, prepare=_prepare_fetch_url)],
    id="kairos-web-v1",
    metadata={"temporal": WEB_TOOL_ACTIVITY_CONFIG},
)
_collection_toolset = FunctionToolset(
    [
        Tool(_agent_fetch_source, name="fetch_source"),
        inspect_snapshot,
        Tool(_agent_commit_extraction, name="commit_extraction"),
        get_collection_progress,
    ],
    id="kairos-collection-v1",
    instructions=(
        "For SPECIFIED_SOURCE collection runs, treat the confirmed CollectionSpec as immutable. "
        "Only process its listed seed sources: fetch_source, then inspect_snapshot in bounded chunks, "
        "then commit_extraction with typed fields and verbatim evidence quotes from the snapshot. "
        "Never fabricate missing values or evidence, never use fetch_url for collection work, and use "
        "records=[] with source_complete=true when a source has no matching record. Check "
        "get_collection_progress before claiming collection completion."
    ),
    metadata={"temporal": COLLECTION_TOOL_ACTIVITY_CONFIG},
)
_search_toolset = FunctionToolset(
    [Tool(_agent_search_sources, name="search_sources")],
    id="kairos-search-v1",
    instructions=(
        "For EXPLORATORY and HYBRID collection runs, use search_sources only to discover public source URLs. "
        "Search snippets are bounded discovery metadata and are never formal evidence or record content. "
        "Process returned actionable sources with fetch_source, inspect_snapshot, and commit_extraction."
    ),
    metadata={"temporal": COLLECTION_TOOL_ACTIVITY_CONFIG},
)

_durability = TemporalDurability(
    event_stream_handler=agent_event_stream_handler,
    deps_type=None,
    activity_config={
        "start_to_close_timeout": timedelta(seconds=60),
        "retry_policy": RetryPolicy(maximum_attempts=2),
    },
    model_activity_config={"start_to_close_timeout": timedelta(seconds=90)},
    event_stream_handler_activity_config={"start_to_close_timeout": timedelta(seconds=30)},
    toolset_activity_config={
        "kairos-web-v1": WEB_TOOL_ACTIVITY_CONFIG,
        "kairos-collection-v1": COLLECTION_TOOL_ACTIVITY_CONFIG,
        "kairos-search-v1": COLLECTION_TOOL_ACTIVITY_CONFIG,
        "kairos-browser-v1": BROWSER_TOOL_ACTIVITY_CONFIG,
        "kairos-workspace-v1": {
            "start_to_close_timeout": timedelta(seconds=60),
            "retry_policy": RetryPolicy(maximum_attempts=2),
        },
    },
)

kairos_agent = Agent(
    f"kairos:{_settings.model_config_id}",
    name=KAIROS_AGENT_NAME,
    deps_type=KairosAgentDeps,
    instructions=(
        "You are the Kairos durable agent. Use tools when the user's request requires real external or "
        "workspace "
        "facts. Never claim a file or URL operation succeeded unless the tool returned success. "
        "The current workspace, when present, is authorized by the run context; use its tools to inspect it."
    ),
    toolsets=[
        _web_toolset,
        _collection_toolset,
        _search_toolset,
        browser_toolset,
        workspace_dynamic_toolset,
    ],
    capabilities=[model_resolver_capability, _durability],
    defer_model_check=True,
)
