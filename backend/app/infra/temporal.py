"""Temporal client + worker construction."""

from __future__ import annotations

import asyncio
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker

from app.config import Settings, get_settings


def _interceptors() -> list[Any]:
    """Wire OpenTelemetry tracing through Temporal (no-op when OTel disabled)."""
    from temporalio.contrib.opentelemetry import TracingInterceptor

    return [TracingInterceptor()]


async def create_temporal_client(settings: Settings) -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        interceptors=_interceptors(),
    )


_client_lock = asyncio.Lock()
_cached_client: Client | None = None


async def get_temporal_client() -> Client:
    """Module-level cached Temporal client (double-checked locking).

    ``create_temporal_client`` is async so it cannot live in a sync
    ``@lru_cache``; reuse one client per process to avoid a fresh connection
    (and TLS handshake) on every request. Each uvicorn worker owns its own
    process-scoped client, so multi-worker deployments do not share state.
    """
    global _cached_client
    if _cached_client is None:
        async with _client_lock:
            if _cached_client is None:
                _cached_client = await create_temporal_client(get_settings())
    return _cached_client


async def create_smoke_worker(client: Client, settings: Settings) -> Worker:
    from app.activities.smoke import write_smoke_record
    from app.workflows.smoke import SmokeWorkflow

    return Worker(
        client,
        task_queue=settings.temporal_smoke_task_queue,
        workflows=[SmokeWorkflow],
        activities=[write_smoke_record],
        interceptors=_interceptors(),
    )


async def create_task_worker(client: Client, settings: Settings) -> Worker:
    from app.activities.approval import block_high_risk_node, request_approval
    from app.activities.plan_execution import execute_safe_unit, fetch_next_execution_unit
    from app.activities.task_execution import (
        commit_checkpoint,
        complete_run,
        ensure_run_started,
        fail_run,
        mark_cancelled,
        mark_paused,
    )
    from app.workflows.task_workflow import TaskWorkflow

    return Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[TaskWorkflow],
        activities=[
            ensure_run_started,
            mark_paused,
            mark_cancelled,
            fail_run,
            complete_run,
            commit_checkpoint,
            fetch_next_execution_unit,
            execute_safe_unit,
            request_approval,
            block_high_risk_node,
        ],
        interceptors=_interceptors(),
    )
