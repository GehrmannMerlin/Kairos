from __future__ import annotations

import asyncio

from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio.client import Client
from temporalio.worker import Worker

from app.activities import persist_agent_event_activity, update_task_run_activity
from app.config import get_settings
from app.workflows import KairosAgentWorkflow


async def run_worker() -> None:
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_target,
        namespace=settings.temporal_namespace,
        plugins=[PydanticAIPlugin()],
    )
    async with Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[KairosAgentWorkflow],
        activities=[persist_agent_event_activity, update_task_run_activity],
    ):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(run_worker())
