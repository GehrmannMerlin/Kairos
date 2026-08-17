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


def _lifecycle_activities() -> list[Any]:
    """core/orchestration queue 上注册的 activity（lifecycle + 审批 + 完成 + reliability）。"""
    from app.activities.approval import (
        block_high_risk_node,
        request_approval,
        resume_from_approval,
    )
    from app.activities.completion import resolve_completion
    from app.activities.credential_approval import resolve_credential_access
    from app.activities.discovery_approval import resolve_robots_override
    from app.activities.plan_execution import execute_safe_unit, fetch_next_execution_unit
    from app.activities.reliability import heartbeat_task_slot, record_resource_wait
    from app.activities.replan import replan_for_continuation
    from app.activities.task_execution import (
        commit_checkpoint,
        complete_run,
        ensure_run_started,
        fail_run,
        mark_cancelled,
        mark_partial,
        mark_paused,
    )

    return [
        ensure_run_started,
        mark_paused,
        mark_cancelled,
        mark_partial,
        fail_run,
        complete_run,
        commit_checkpoint,
        fetch_next_execution_unit,
        execute_safe_unit,
        request_approval,
        block_high_risk_node,
        resume_from_approval,
        resolve_credential_access,
        resolve_robots_override,
        resolve_completion,
        replan_for_continuation,
        record_resource_wait,
        heartbeat_task_slot,
    ]


async def create_role_worker(
    client: Client,
    settings: Settings,
    *,
    queue: str,
    workflows: list[Any],
    activities: list[Any],
    max_concurrent_activities: int,
) -> Worker:
    """同一代码库不同 role 的 Temporal Worker（I-001 §3）：queue + max_concurrent 来自容量配置。"""
    return Worker(
        client,
        task_queue=queue,
        workflows=workflows,
        activities=activities,
        interceptors=_interceptors(),
        max_concurrent_activities=max_concurrent_activities,
    )


async def create_task_workers(client: Client, settings: Settings) -> list[Worker]:
    """按 KAIROS_WORKER_ROLES 创建 role worker（同代码库不同 queue/concurrency）。

    core 队列承载 TaskWorkflow + lifecycle；HTTP/BROWSER/LLM_SEARCH 队列只注册
    execute_safe_unit（按 ResourceClass 确定性路由进入）。default all → 单进程全部 queue。
    """
    from app.activities.plan_execution import execute_safe_unit
    from app.reliability.capacity import capacity_from_settings
    from app.reliability.pools import (
        WorkerRole,
        capacity_pool_for_queue,
        parse_worker_roles,
        role_task_queues,
    )
    from app.workflows.task_workflow import TaskWorkflow

    roles = parse_worker_roles(settings.worker_roles)
    if WorkerRole.ALL in roles:
        roles = [WorkerRole.CORE, WorkerRole.HTTP, WorkerRole.BROWSER, WorkerRole.LLM_SEARCH]
    capacity = capacity_from_settings(settings)
    workers: list[Worker] = []
    for role in roles:
        for queue in role_task_queues(role):
            if queue == settings.temporal_task_queue:
                workers.append(
                    await create_role_worker(
                        client, settings, queue=queue,
                        workflows=[TaskWorkflow], activities=_lifecycle_activities(),
                        max_concurrent_activities=capacity_pool_for_queue(queue, capacity),
                    )
                )
            else:
                workers.append(
                    await create_role_worker(
                        client, settings, queue=queue,
                        workflows=[], activities=[execute_safe_unit],
                        max_concurrent_activities=capacity_pool_for_queue(queue, capacity),
                    )
                )
    return workers
