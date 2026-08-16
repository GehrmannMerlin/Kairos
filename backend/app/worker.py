"""Temporal Worker entrypoint.

Run with: ``python -m app.worker``
The same codebase will host different worker roles via task queues / startup
arguments in later modules (D-026 / I-001).
"""

from __future__ import annotations

import asyncio

# Register every ORM table in the shared Base.metadata before any Activity flushes
# a row. Domain models carry ``ForeignKey("users.id")`` on every user_id column,
# and SQLAlchemy must be able to resolve the ``users`` target table. The API gets
# this transitively through its route modules; the worker must import it directly.
from app.auth.models import User  # noqa: F401
from app.config import get_settings
from app.infra.telemetry import setup_otel


async def run() -> None:
    settings = get_settings()
    from app.observability.logging import configure_logging

    configure_logging("kairos-worker")
    setup_otel(settings)
    from app.infra.temporal import create_temporal_client

    client = await create_temporal_client(settings)
    # Staging Gate fixture harness（DEPLOY-GATE-2 / M-08 §48）：仅 plan_fixture_mode 时注册。
    if settings.plan_fixture_mode:
        from app.plan.staging_fixture import install_staging_fixture

        install_staging_fixture()
        print("kairos worker: plan_fixture_mode enabled (staging fixture executor)")
    # M-16：全部 role 都安装全量 executor；role 只决定 poll 哪些 TaskQueue + 并发，
    # 不复制四份 Worker 工程（I-001 §3）。
    from app.artifacts.executor import install_artifact_executor
    from app.crawling.executors import install_fetch_executors
    from app.discovery.executors import install_discovery_executors
    from app.extraction.executors import install_extraction_executors
    from app.plan.capabilities import assert_runtime_executor_manifest
    from app.validation.executors import install_validation_executors

    install_artifact_executor()
    install_discovery_executors()
    install_fetch_executors()
    install_extraction_executors()
    install_validation_executors()
    assert_runtime_executor_manifest()

    from app.infra.temporal import (
        create_smoke_worker,
        create_task_workers,
        create_temporal_client,
    )
    from app.reliability.pools import parse_worker_roles

    smoke_worker = await create_smoke_worker(client, settings)
    workers = await create_task_workers(client, settings)
    roles = parse_worker_roles(settings.worker_roles)
    print(
        f"kairos worker roles={[r.value for r in roles]} "
        f"queues={[w.task_queue for w in workers]} "
        f"({settings.temporal_address}, smoke={settings.temporal_smoke_task_queue})"
    )
    await asyncio.gather(smoke_worker.run(), *(w.run() for w in workers))


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
