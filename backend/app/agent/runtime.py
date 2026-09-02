from __future__ import annotations

from datetime import timedelta

from pydantic_ai import Agent, FunctionToolset
from pydantic_ai.durable_exec.temporal import TemporalDurability
from temporalio.common import RetryPolicy

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
)
from app.config import get_settings
from app.provider import model_resolver_capability
from app.workspace import workspace_dynamic_toolset

KAIROS_AGENT_NAME = "kairos-agent-v1"

_settings = get_settings()
_web_toolset = FunctionToolset(
    [fetch_url],
    id="kairos-web-v1",
    metadata={"temporal": WEB_TOOL_ACTIVITY_CONFIG},
)
_collection_toolset = FunctionToolset(
    [fetch_source, inspect_snapshot, commit_extraction, get_collection_progress],
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
    toolsets=[_web_toolset, _collection_toolset, workspace_dynamic_toolset],
    capabilities=[model_resolver_capability, _durability],
    defer_model_check=True,
)
