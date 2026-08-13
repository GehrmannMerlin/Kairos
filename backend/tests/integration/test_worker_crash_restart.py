"""Worker 崩溃/重启恢复：batch1 commit+checkpoint 后 kill worker，重启后 batch1 不
重复、batch2 完成、最终结果一次。必须用真实子进程 kill，不得直接调 Activity 两次。"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from app.config import get_settings
from app.domain.models import Checkpoint, Run, Task
from app.infra.deps import get_session_factory
from app.infra.temporal import create_temporal_client
from app.workflows.starter import TaskWorkflowStarter
from app.workflows.task_workflow import TaskWorkflowResult

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]


def _spawn_worker(queue: str) -> subprocess.Popen:
    env = dict(os.environ)
    return subprocess.Popen(
        [sys.executable, "-m", "tests.integration.fixture_worker", queue],
        cwd=str(ROOT / "backend"),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _kill_worker(proc: subprocess.Popen) -> None:
    """Force-kill the worker subprocess (真实崩溃).

    POSIX uses SIGKILL. Windows has no SIGKILL and Popen.send_signal only supports
    SIGTERM, so use proc.kill() (TerminateProcess), which is the Windows equivalent.
    """
    if os.name == "nt":
        proc.kill()
    else:
        proc.send_signal(signal.SIGKILL)  # type: ignore[attr-defined]  # POSIX-only


def _wait_checkpoint(run_id: int, batch: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        session = get_session_factory()()
        try:
            exists = (
                session.query(Checkpoint).filter_by(run_id=run_id, batch_identity=batch).first()
            )
        finally:
            session.close()
        if exists:
            return
        time.sleep(0.2)
    raise TimeoutError(f"checkpoint {batch} not committed")


@pytest.mark.asyncio
async def test_worker_crash_restart_no_duplicate_batch(confirmed_task) -> None:
    settings = get_settings()
    queue = f"kairos-test-crash-{uuid4().hex[:8]}"
    client = await create_temporal_client(settings)
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.start(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
        task_queue=queue,  # 启动到测试专用 queue，fixture worker 监听同一 queue
    )

    proc = _spawn_worker(queue)
    try:
        _wait_checkpoint(started.run_id, "unit-1")  # batch1 已提交
        _kill_worker(proc)  # 崩溃
        proc.wait(timeout=10)
        await asyncio.sleep(1)
        proc = _spawn_worker(queue)  # 重启

        handle = client.get_workflow_handle(started.workflow_id, result_type=TaskWorkflowResult)
        result: TaskWorkflowResult = await handle.result(rpc_timeout=timedelta(seconds=90))

        session = get_session_factory()()
        try:
            cps = (
                session.query(Checkpoint)
                .filter_by(run_id=started.run_id)
                .order_by(Checkpoint.id)
                .all()
            )
            assert len(cps) == 3  # unit-1/2/3 各一次
            run = session.get(Run, started.run_id)
            task = session.get(Task, confirmed_task["task_id"])
            assert run is not None and task is not None
            assert run.state == "completed"
            assert task.state == "COMPLETED"
        finally:
            session.close()
        assert result.final_state == "COMPLETED"
    finally:
        if proc.poll() is None:
            proc.kill()
