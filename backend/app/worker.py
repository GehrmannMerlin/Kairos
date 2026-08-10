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
from app.infra.temporal import create_smoke_worker, create_task_worker, create_temporal_client


async def run() -> None:
    settings = get_settings()
    setup_otel(settings)
    client = await create_temporal_client(settings)
    smoke_worker = await create_smoke_worker(client, settings)
    task_worker = await create_task_worker(client, settings)
    print(
        f"kairos worker listening on {settings.temporal_address} "
        f"(smoke={settings.temporal_smoke_task_queue}, task={settings.temporal_task_queue})"
    )
    await asyncio.gather(smoke_worker.run(), task_worker.run())


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
