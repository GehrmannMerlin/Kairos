from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Any

from pydantic_ai import AgentStreamEvent, FunctionToolCallEvent, FunctionToolResultEvent, RunContext
from pydantic_ai.messages import RetryPromptPart

from app.domain import EventEnvelope, bounded_payload
from app.repositories import insert_agent_event


def envelope_from_pydantic_event(ctx: RunContext[Any], event: AgentStreamEvent) -> EventEnvelope | None:
    deps = ctx.deps
    common = {
        "task_id": deps.task_id,
        "task_run_id": deps.task_run_id,
        "owner_id": deps.user_id,
        "agent_name": "kairos-agent-v1",
    }
    if isinstance(event, FunctionToolCallEvent):
        tool_name = event.part.tool_name
        event_type = "tool.failed" if event.args_valid is False else "tool.started"
        return EventEnvelope(
            **common,
            event_type=event_type,
            tool_name=tool_name,
            summary=f"{tool_name} started"
            if event_type == "tool.started"
            else f"{tool_name} arguments rejected",
            payload=bounded_payload({"args_valid": event.args_valid}),
        )
    if isinstance(event, FunctionToolResultEvent):
        tool_name = event.part.tool_name
        if isinstance(event.part, RetryPromptPart):
            failed = True
            result_text = event.part.model_response()
        else:
            failed = getattr(event.part, "outcome", None) == "failed"
            result_text = event.part.model_response_str(wrap_if_error=False)
        return EventEnvelope(
            **common,
            event_type="tool.failed" if failed else "tool.completed",
            tool_name=tool_name,
            summary=f"{tool_name} failed" if failed else f"{tool_name} completed",
            payload=bounded_payload(
                {
                    "outcome": getattr(event.part, "outcome", None),
                    "result_chars": len(result_text),
                }
            ),
        )
    return None


async def agent_event_stream_handler(ctx: RunContext[Any], stream: AsyncIterable[AgentStreamEvent]) -> None:
    """Persist event side effects in the Activity registered by TemporalDurability."""
    async for event in stream:
        envelope = envelope_from_pydantic_event(ctx, event)
        if envelope is not None:
            await insert_agent_event(envelope)
