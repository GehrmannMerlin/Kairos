"""Temporal Worker entrypoint.

Run with: ``python -m app.worker``
The same codebase will host different worker roles via task queues / startup
arguments in later modules (D-026 / I-001).
"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.infra.telemetry import setup_otel
from app.infra.temporal import create_smoke_worker, create_temporal_client


async def run() -> None:
    settings = get_settings()
    setup_otel(settings)
    client = await create_temporal_client(settings)
    worker = await create_smoke_worker(client, settings)
    queue = settings.temporal_smoke_task_queue
    print(f"kairos worker listening on {settings.temporal_address} (queue={queue})")
    await worker.run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
