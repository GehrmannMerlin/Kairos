"""Owner-scoped browser domain events (persisted via the existing event path).

Browser events flow through the same PostgreSQL `agent_events` table and FastAPI
SSE as every other collection event; the child Browser Agent never pushes over
the network. Payloads are strictly bounded and secret-free.
"""

from __future__ import annotations

from app.domain import EventEnvelope, bounded_payload
from app.repositories import insert_agent_event

# Bounded high-level action events users can follow in the execution timeline.
# Never chain-of-thought, never full selectors, never HTML, never model output.
_ACTION_EVENT_LIMIT = 30


async def emit_browser_event(
    *,
    task_id: str,
    task_run_id: str,
    owner_id: str,
    event_type: str,
    summary: str,
    payload: dict[str, object] | None = None,
    agent_name: str = "kairos-agent-v1",
) -> None:
    """Persist one bounded browser.* event through the standard event path."""
    await insert_agent_event(
        EventEnvelope(
            event_type=event_type,
            task_id=task_id,
            task_run_id=task_run_id,
            owner_id=owner_id,
            agent_name=agent_name,
            summary=summary[:2000],
            payload=bounded_payload(payload or {}),
        )
    )


def cap_action_events(
    actions: list[dict[str, object]], limit: int = _ACTION_EVENT_LIMIT
) -> list[dict[str, object]]:
    """Aggregate action events beyond `limit` into a single bounded summary (phase4 §90)."""
    if len(actions) <= limit:
        return actions
    head = list(actions[:limit])
    tail_count = len(actions) - limit
    head.append(
        {
            "action_type": "aggregated",
            "short_description": f"{tail_count} more read-only browser actions",
        }
    )
    return head