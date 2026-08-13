"""Temporal TaskWorkflow integration (requires KAIROS_RUN_INTEGRATION=1 + local stack)."""

from __future__ import annotations

import asyncio
import contextlib
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
from app.domain.models import Checkpoint, OutboxEvent, Run, Task
from app.domain.repository import TaskRepository
from app.domain.task_commands import TaskCommandService
from app.infra.deps import get_session_factory
from app.infra.outbox_dispatch import OutboxTemporalDispatcher
from app.infra.temporal import create_temporal_client
from app.workflows.starter import TaskWorkflowStarter
from app.workflows.task_workflow import TaskWorkflowResult

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]


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


def _spawn_fixture_worker(queue: str) -> subprocess.Popen:
    env = dict(os.environ)
    return subprocess.Popen(
        [sys.executable, "-m", "tests.integration.fixture_worker", queue],
        cwd=str(ROOT / "backend"),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _kill_worker(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        proc.kill()
    else:
        proc.send_signal(signal.SIGKILL)  # type: ignore[attr-defined]  # POSIX-only


async def _send_command(
    client, *, user_id: int, task_id: int, command: str, idempotency_key: str | None = None
) -> None:
    """走真实命令路径：TaskCommandService（幂等+状态机+outbox）→ dispatcher（Signal）。"""
    session = get_session_factory()()
    try:
        task = TaskRepository(session).get_owned(user_id, task_id)
        svc = TaskCommandService(session)
        handler = {
            "pause": svc.pause_task,
            "resume": svc.resume_task,
            "cancel": svc.cancel_task,
        }[command]
        handler(
            user_id=user_id,
            task_id=task_id,
            expected_version=task.version,
            idempotency_key=idempotency_key,
        )
        await OutboxTemporalDispatcher(client).dispatch_pending_for(
            session, user_id=user_id, task_id=task_id
        )
    finally:
        session.close()


@pytest.mark.asyncio
async def test_start_workflow_creates_run_and_running(confirmed_task) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    # 专用队列 + fixture worker，避免依赖外部生产 worker（其不注册 seam activities，
    # 会导致 ensure_run_started 后的 fetch_next_execution_unit 失败并和断言赛跑）。
    queue = f"kairos-test-start-{uuid4().hex[:8]}"
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.start(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
        task_queue=queue,
    )
    proc = _spawn_fixture_worker(queue)
    try:
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

        # 清理：fixture worker 会执行 seam activities 进入执行循环，这里终止避免遗留。
        with contextlib.suppress(Exception):
            await handle.terminate(reason="test cleanup")
    finally:
        if proc.poll() is None:
            _kill_worker(proc)


@pytest.mark.asyncio
async def test_pause_resume_no_duplicate(confirmed_task) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    queue = f"kairos-test-pause-{uuid4().hex[:8]}"
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.start(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
        task_queue=queue,
    )
    proc = _spawn_fixture_worker(queue)
    try:
        _wait_task_state(confirmed_task["task_id"], "RUNNING")
        # 真实命令：RUNNING→PAUSING（状态机）+ outbox + Signal → Workflow mark_paused → PAUSED
        await _send_command(
            client,
            user_id=confirmed_task["user_id"],
            task_id=confirmed_task["task_id"],
            command="pause",
        )
        _wait_task_state(confirmed_task["task_id"], "PAUSED")

        session = get_session_factory()()
        try:
            task = session.get(Task, confirmed_task["task_id"])
            assert task.state == "PAUSED"
        finally:
            session.close()

        await _send_command(
            client,
            user_id=confirmed_task["user_id"],
            task_id=confirmed_task["task_id"],
            command="resume",
        )
        handle = client.get_workflow_handle(started.workflow_id, result_type=TaskWorkflowResult)
        result = await handle.result(rpc_timeout=timedelta(seconds=90))

        session = get_session_factory()()
        try:
            cps = (
                session.query(Checkpoint)
                .filter_by(run_id=started.run_id)
                .order_by(Checkpoint.id)
                .all()
            )
            assert len(cps) == 3  # 无重复：unit-1/2/3 各一次
            run = session.get(Run, started.run_id)
            assert run.state == "completed"
        finally:
            session.close()
        assert result.final_state == "COMPLETED"
    finally:
        if proc.poll() is None:
            _kill_worker(proc)


@pytest.mark.asyncio
async def test_pause_timeout_keeps_task_paused(confirmed_task) -> None:
    """pause_timeout 是复检间隔而非硬截止（final review Finding 1）。

    超时后任务必须保持 PAUSED（Run 不得写成 failed 矛盾终态），恢复后正常完成。
    """
    settings = get_settings()
    client = await create_temporal_client(settings)
    queue = f"kairos-test-pausetmo-{uuid4().hex[:8]}"
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.start(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
        task_queue=queue,
        pause_timeout_seconds=1,
    )
    proc = _spawn_fixture_worker(queue)
    try:
        _wait_task_state(confirmed_task["task_id"], "RUNNING")
        await _send_command(
            client,
            user_id=confirmed_task["user_id"],
            task_id=confirmed_task["task_id"],
            command="pause",
        )
        _wait_task_state(confirmed_task["task_id"], "PAUSED")

        # 等待超过 pause_timeout=1s：超时只是复检，任务应保持 PAUSED、Run 非 failed。
        await asyncio.sleep(1.6)
        session = get_session_factory()()
        try:
            task = session.get(Task, confirmed_task["task_id"])
            run = session.get(Run, started.run_id)
            assert task.state == "PAUSED"
            assert run.state != "failed"
        finally:
            session.close()

        # 恢复后任务应正常完成（不是 FAILED 死路）。
        await _send_command(
            client,
            user_id=confirmed_task["user_id"],
            task_id=confirmed_task["task_id"],
            command="resume",
        )
        handle = client.get_workflow_handle(started.workflow_id, result_type=TaskWorkflowResult)
        result = await handle.result(rpc_timeout=timedelta(seconds=90))

        session = get_session_factory()()
        try:
            run = session.get(Run, started.run_id)
            assert run.state == "completed"
        finally:
            session.close()
        assert result.final_state == "COMPLETED"
    finally:
        if proc.poll() is None:
            _kill_worker(proc)


@pytest.mark.asyncio
async def test_cancel_keeps_committed(confirmed_task) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    queue = f"kairos-test-cancel-{uuid4().hex[:8]}"
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.start(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
        task_queue=queue,
    )
    proc = _spawn_fixture_worker(queue)
    try:
        _wait_task_state(confirmed_task["task_id"], "RUNNING")
        await _send_command(
            client,
            user_id=confirmed_task["user_id"],
            task_id=confirmed_task["task_id"],
            command="cancel",
        )
        handle = client.get_workflow_handle(started.workflow_id, result_type=TaskWorkflowResult)
        result = await handle.result(rpc_timeout=timedelta(seconds=90))

        session = get_session_factory()()
        try:
            task = session.get(Task, confirmed_task["task_id"])
            run = session.get(Run, started.run_id)
            cps = session.query(Checkpoint).filter_by(run_id=started.run_id).all()
            assert task.state == "CANCELLED"
            assert run.state == "cancelled"
            assert len(cps) >= 1  # 已提交 batch 保留
            assert len(cps) < 3  # 未提交 batch 不算成功（取消时尚未跑完）
        finally:
            session.close()
        assert result.final_state == "CANCELLED"
    finally:
        if proc.poll() is None:
            _kill_worker(proc)


@pytest.mark.asyncio
async def test_duplicate_command_idempotent(confirmed_task) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    queue = f"kairos-test-dup-{uuid4().hex[:8]}"
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.start(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
        task_queue=queue,
    )
    proc = _spawn_fixture_worker(queue)
    try:
        _wait_task_state(confirmed_task["task_id"], "RUNNING")
        key = "dup-pause-1"
        await _send_command(
            client,
            user_id=confirmed_task["user_id"],
            task_id=confirmed_task["task_id"],
            command="pause",
            idempotency_key=key,
        )
        # 同一 key 重复 pause：幂等（第二次不产生新转换/新 outbox/Signal 副作用）
        await _send_command(
            client,
            user_id=confirmed_task["user_id"],
            task_id=confirmed_task["task_id"],
            command="pause",
            idempotency_key=key,
        )
        _wait_task_state(confirmed_task["task_id"], "PAUSED")

        session = get_session_factory()()
        try:
            task = session.get(Task, confirmed_task["task_id"])
            _ = task.version  # 幂等暂停后仍可读取；仅确认不产生新副作用
        finally:
            session.close()

        await _send_command(
            client,
            user_id=confirmed_task["user_id"],
            task_id=confirmed_task["task_id"],
            command="resume",
        )
        handle = client.get_workflow_handle(started.workflow_id, result_type=TaskWorkflowResult)
        result = await handle.result(rpc_timeout=timedelta(seconds=90))

        session = get_session_factory()()
        try:
            cps = session.query(Checkpoint).filter_by(run_id=started.run_id).all()
            assert len(cps) == 3  # 未因重复命令产生重复业务效果
            outbox_dups = (
                session.query(OutboxEvent)
                .filter(
                    OutboxEvent.aggregate_id == confirmed_task["task_id"],
                    OutboxEvent.event_type == "task.pause",
                )
                .count()
            )
            assert outbox_dups <= 1  # 同一幂等 key 只入队一次 outbox
        finally:
            session.close()
        assert result.final_state == "COMPLETED"
    finally:
        if proc.poll() is None:
            _kill_worker(proc)
