"""Run the real-model Temporal smoke; never substitutes TestModel for acceptance."""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import uuid4

from app.agent.deps import KairosAgentDeps
from app.config import get_settings
from app.domain import WorkspacePermission
from app.workflows import KairosAgentWorkflow, KairosAgentWorkflowInput
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio.client import Client


async def main() -> int:
    settings = get_settings()
    if not os.getenv(settings.model_credential_env):
        print("real-model acceptance blocked by credential")
        return 2

    task_id = f"smoke-task-{uuid4().hex}"
    run_id = f"smoke-run-{uuid4().hex}"
    workflow_id = f"kairos-real-agent-smoke-{uuid4().hex}"
    deps = KairosAgentDeps(
        user_id="smoke-user",
        task_id=task_id,
        task_run_id=run_id,
        model_config_id=settings.model_config_id,
        workspace_permission=WorkspacePermission.NONE,
    )
    client = await Client.connect(
        settings.temporal_target,
        namespace=settings.temporal_namespace,
        plugins=[PydanticAIPlugin()],
    )
    handle = await client.start_workflow(
        KairosAgentWorkflow.run,
        KairosAgentWorkflowInput(
            deps=deps,
            prompt="访问 https://example.com，并告诉我页面标题和主要内容。必须使用 fetch_url 工具获取真实页面。",
        ),
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
    )
    result = await handle.result()
    print(
        {
            "workflow_id": workflow_id,
            "agent": "kairos-agent-v1",
            "task_run_id": result.task_run_id,
        }
    )
    print({"final_answer": result.answer})
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
