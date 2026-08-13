"""Shared executor event emission (aggregate extraction/normalize events, D-039)."""

from __future__ import annotations

from typing import Any


def emit_event(db: Any, run: Any, event_type: str, payload: dict) -> None:
    """Append a domain event in the same transaction (caller commits once)."""
    from app.state.events import append_domain_event

    append_domain_event(
        db,
        user_id=run.user_id,
        aggregate_type="task",
        aggregate_id=run.task_id,
        event_type=event_type,
        aggregate_version=1,
        payload=payload,
        actor_type="system",
        run_id=run.id,
        node_run_id=None,
    )
