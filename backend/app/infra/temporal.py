"""Temporal client + worker construction."""

from __future__ import annotations

from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker

from app.config import Settings


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
    from app.activities.task_execution import (
        commit_checkpoint,
        complete_run,
        ensure_run_started,
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
            complete_run,
            commit_checkpoint,
        ],
        interceptors=_interceptors(),
    )
