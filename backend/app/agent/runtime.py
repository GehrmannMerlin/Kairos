from __future__ import annotations

from datetime import timedelta

from pydantic_ai import Agent, FunctionToolset
from pydantic_ai.durable_exec.temporal import TemporalDurability
from temporalio.common import RetryPolicy

from app.agent.deps import KairosAgentDeps
from app.agent.events import agent_event_stream_handler
from app.agent.tools import WEB_TOOL_ACTIVITY_CONFIG, fetch_url
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
    toolsets=[_web_toolset, workspace_dynamic_toolset],
    capabilities=[model_resolver_capability, _durability],
    defer_model_check=True,
)
