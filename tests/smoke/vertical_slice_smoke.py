"""Exercise the HTTP boundary; it expects the real API, Worker, DB, and Temporal services."""

from __future__ import annotations

import asyncio
import os
import sys

import httpx


async def main() -> int:
    base_url = os.getenv("KAIROS_API_URL", "http://127.0.0.1:8000")
    workspace_path = os.getenv("KAIROS_SMOKE_WORKSPACE")
    if not workspace_path:
        print(
            "vertical slice blocked: set KAIROS_SMOKE_WORKSPACE to an existing local directory"
        )
        return 2

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        health = await client.get("/health")
        health.raise_for_status()
        task = (await client.post("/api/tasks")).json()
        workspace = (
            await client.post(
                "/api/workspaces",
                json={
                    "display_name": "Vertical Smoke Workspace",
                    "root_path": workspace_path,
                    "permission_mode": "READ_WRITE",
                },
            )
        ).json()
        await client.post(
            f"/api/tasks/{task['task_id']}/workspace",
            params={"workspace_id": workspace["workspace_id"]},
        )
        run = (
            await client.post(
                f"/api/tasks/{task['task_id']}/runs",
                json={
                    "prompt": (
                        "检查当前工作区，创建 kairos-agent-test.md，内容必须精确为："
                        "Kairos durable workspace acceptance test. 然后重新读取并确认写入成功。"
                    )
                },
            )
        ).json()
        print({"task_id": task["task_id"], "workflow_id": run["workflow_id"]})
        async with client.stream(
            "GET", f"/api/tasks/{task['task_id']}/events"
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith(("event:", "data:")):
                    print(line)
                if line == "event: run.completed":
                    break
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
