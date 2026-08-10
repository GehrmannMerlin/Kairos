"""Temporal TaskWorkflow integration (requires KAIROS_RUN_INTEGRATION=1 + local stack)."""

from __future__ import annotations

import contextlib
import time

import pytest
from app.config import get_settings
from app.domain.models import Run, Task
from app.infra.deps import get_session_factory
from app.infra.temporal import create_temporal_client
from app.workflows.starter import TaskWorkflowStarter

pytestmark = pytest.mark.integration


def _wait_task_state(task_id: int, want: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        session = get_session_factory()()
        try:
            task = session.get(Task, task_id)
            if task is not None and task.state == want:
                return
        finally:
            session.close()
        time.sleep(0.2)
    raise TimeoutError(f"task {task_id} did not reach {want}")


@pytest.mark.asyncio
async def test_start_workflow_creates_run_and_running(confirmed_task) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.start(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
    )
    # starter 创建 pending Run 并返回 run_id
    assert started.run_id > 0
    assert started.workflow_id == f"task-workflow-{confirmed_task['task_id']}"

    # ensure_run_started Activity 幂等激活：Task QUEUED->RUNNING、Run started。
    _wait_task_state(confirmed_task["task_id"], "RUNNING")

    handle = client.get_workflow_handle(started.workflow_id)
    desc = await handle.describe()
    assert desc.status.name in ("RUNNING", "COMPLETED")  # 至少已启动/在跑

    session = get_session_factory()()
    try:
        run = session.get(Run, started.run_id)
        task = session.get(Task, confirmed_task["task_id"])
        assert run.state == "running"
        assert run.started_at is not None
        assert task.state == "RUNNING"
    finally:
        session.close()

    # 清理：fixture 执行单元在 Task 4 才注册，这里终止 workflow 避免遗留。
    with contextlib.suppress(Exception):
        await handle.terminate(reason="test cleanup")
