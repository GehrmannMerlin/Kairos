"""Append-only DomainEvent + transactional Outbox enqueue (M-04).

These helpers are called inside the SAME db transaction as the state change;
the caller commits once. Never UPDATE a historical event.
"""

from __future__ import annotations

from typing import Any

from app.domain.models import DomainEvent, OutboxEvent


def append_domain_event(
    db: Any,
    *,
    user_id: int,
    aggregate_type: str,
    aggregate_id: int,
    event_type: str,
    aggregate_version: int,
    payload: dict,
    actor_type: str = "user",
    actor_id: int | None = None,
    run_id: int | None = None,
    node_run_id: int | None = None,
) -> DomainEvent:
    row = DomainEvent(
        user_id=user_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        aggregate_version=aggregate_version,
        payload=payload,
        actor_type=actor_type,
        actor_id=actor_id,
        run_id=run_id,
        node_run_id=node_run_id,
    )
    db.add(row)
    return row


def enqueue_outbox(
    db: Any,
    *,
    user_id: int,
    aggregate_type: str,
    aggregate_id: int,
    event_type: str,
    payload: dict,
    dispatch_key: str | None = None,
) -> OutboxEvent:
    row = OutboxEvent(
        user_id=user_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload,
        status="pending",
        dispatch_key=dispatch_key,
    )
    db.add(row)
    return row
