"""M-08 Temporal integration: plan → workflow → approval wait/resume (KAIROS_RUN_INTEGRATION=1).

A: VALID low-risk plan → workflow starts → no second confirmation → COMPLETED.
B: high-risk fixture node → Approval PENDING → approve → workflow resumes → node executes.
C: high-risk fixture → reject → high-risk operation never runs → node BLOCKED.
"""

from __future__ import annotations

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
from app.domain.models import Checkpoint, Task
from app.infra.deps import get_session_factory
from app.infra.outbox_dispatch import OutboxTemporalDispatcher
from app.infra.temporal import create_temporal_client
from app.plan.service import PlanService, plan_fingerprint
from app.workflows.starter import TaskWorkflowStarter
from app.workflows.task_workflow import TaskWorkflowResult

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]


def _wait_task_state(task_id: int, want: str, timeout: float = 40.0) -> None:
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


def _spawn_plan_worker(queue: str) -> subprocess.Popen:
    env = dict(os.environ)
    return subprocess.Popen(
        [sys.executable, "-m", "tests.integration.fixture_plan_worker", queue],
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


def _wait_approval(user_id: int, task_id: int) -> int:
    deadline = time.time() + 40.0
    while time.time() < deadline:
        session = get_session_factory()()
        try:
            from app.approval.service import ApprovalService

            pending = ApprovalService(session).list_pending_for_task(user_id, task_id)
            if pending:
                return pending[0].id
        finally:
            session.close()
        time.sleep(0.2)
    raise TimeoutError(f"task {task_id} has no pending approval")


def _make_plan(
    task_id: int,
    *,
    high_risk: bool,
    user_id: int = 1,
    spec_version: int = 1,
) -> int:
    """Persist a PlanVersion for the task (low-risk or high-risk graph)."""
    session = get_session_factory()()
    try:
        from app.workflows.starter import TaskWorkflowStarter  # noqa: F401

        class _NoopStarter:
            async def submit_validated_plan(self, **kw):
                return type("R", (), {"run_id": 0, "workflow_id": ""})()

        graph = {
            "nodes": [
                {
                    "node_id": "n1",
                    "node_type": "fetch",
                    "definition_version": "1.0.0",
                    "parameters": (
                        {
                            "url_template": "https://example.com/private/{id}",
                            "non_public": True,
                        }
                        if high_risk
                        else {"url_template": "https://example.com/{id}"}
                    ),
                    "depends_on": [],
                },
                {
                    "node_id": "n2",
                    "node_type": "extract",
                    "definition_version": "1.0.0",
                    "parameters": {"fields": ["公司名"]},
                    "depends_on": ["n1"],
                },
            ],
            "node_risk_levels": {
                "n1": "high" if high_risk else "low",
                "n2": "low",
            },
        }
        registry_versions = {"fetch": "1.0.0", "extract": "1.0.0"}
        svc = PlanService(session, starter=_NoopStarter())
        row = svc.persist_plan(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            graph=graph,
            validation_status="VALID" if not high_risk else "REQUIRES_APPROVAL",
            fingerprint_value=plan_fingerprint(graph, registry_versions),
            registry_versions=registry_versions,
        )
        return row.version
    finally:
        session.close()


async def _resolve_approval(
    client, *, user_id: int, task_id: int, approval_id: int, decision: str
) -> None:
    session = get_session_factory()()
    try:
        from app.approval.service import ApprovalService

        svc = ApprovalService(session)
        if decision == "approve":
            svc.approve(user_id=user_id, approval_id=approval_id, actor_id=user_id)
        else:
            svc.reject(user_id=user_id, approval_id=approval_id, actor_id=user_id)
        await OutboxTemporalDispatcher(client).dispatch_pending_for(
            session, user_id=user_id, task_id=task_id
        )
    finally:
        session.close()


@pytest.mark.asyncio
async def test_valid_low_risk_plan_starts_without_confirmation(confirmed_task) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    queue = f"kairos-test-plan-a-{uuid4().hex[:8]}"
    plan_version = _make_plan(
        confirmed_task["task_id"], high_risk=False, user_id=confirmed_task["user_id"]
    )
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.submit_validated_plan(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
        plan_version=plan_version,
    )
    proc = _spawn_plan_worker(queue)
    try:
        _wait_task_state(confirmed_task["task_id"], "RUNNING")
        handle = client.get_workflow_handle(started.workflow_id, result_type=TaskWorkflowResult)
        result = await handle.result(rpc_timeout=timedelta(seconds=90))
        assert result.final_state == "COMPLETED"
    finally:
        if proc.poll() is None:
            _kill_worker(proc)


@pytest.mark.asyncio
async def test_high_risk_node_approval_wait_and_resume(confirmed_task) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    queue = f"kairos-test-plan-b-{uuid4().hex[:8]}"
    plan_version = _make_plan(
        confirmed_task["task_id"], high_risk=True, user_id=confirmed_task["user_id"]
    )
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.submit_validated_plan(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
        plan_version=plan_version,
    )
    proc = _spawn_plan_worker(queue)
    try:
        # Workflow 到达高风险 Node → WAITING_APPROVAL + PENDING Approval
        _wait_task_state(confirmed_task["task_id"], "WAITING_APPROVAL")
        approval_id = _wait_approval(confirmed_task["user_id"], confirmed_task["task_id"])
        # 批准 → Signal → Workflow 恢复执行 → fetch fixture 完成 → COMPLETED
        await _resolve_approval(
            client,
            user_id=confirmed_task["user_id"],
            task_id=confirmed_task["task_id"],
            approval_id=approval_id,
            decision="approve",
        )
        handle = client.get_workflow_handle(started.workflow_id, result_type=TaskWorkflowResult)
        result = await handle.result(rpc_timeout=timedelta(seconds=90))
        assert result.final_state == "COMPLETED"

        session = get_session_factory()()
        try:
            cps = (
                session.query(Checkpoint)
                .filter_by(run_id=started.run_id)
                .order_by(Checkpoint.id)
                .all()
            )
            # n1 fetch 被执行（committed_refs 含 node_type=fetch）
            assert any(
                "fetch" in (cp.committed_object_refs or {}).get("node_type", "") for cp in cps
            )
        finally:
            session.close()
    finally:
        if proc.poll() is None:
            _kill_worker(proc)


@pytest.mark.asyncio
async def test_high_risk_node_reject_never_executes(confirmed_task) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    queue = f"kairos-test-plan-c-{uuid4().hex[:8]}"
    plan_version = _make_plan(
        confirmed_task["task_id"], high_risk=True, user_id=confirmed_task["user_id"]
    )
    starter = TaskWorkflowStarter(client, settings)
    started = await starter.submit_validated_plan(
        user_id=confirmed_task["user_id"],
        task_id=confirmed_task["task_id"],
        spec_version=confirmed_task["spec_version"],
        plan_version=plan_version,
    )
    proc = _spawn_plan_worker(queue)
    try:
        _wait_task_state(confirmed_task["task_id"], "WAITING_APPROVAL")
        approval_id = _wait_approval(confirmed_task["user_id"], confirmed_task["task_id"])
        await _resolve_approval(
            client,
            user_id=confirmed_task["user_id"],
            task_id=confirmed_task["task_id"],
            approval_id=approval_id,
            decision="reject",
        )
        handle = client.get_workflow_handle(started.workflow_id, result_type=TaskWorkflowResult)
        result = await handle.result(rpc_timeout=timedelta(seconds=90))
        assert result.final_state == "COMPLETED"

        session = get_session_factory()()
        try:
            cps = (
                session.query(Checkpoint)
                .filter_by(run_id=started.run_id)
                .order_by(Checkpoint.id)
                .all()
            )
            # 高风险 Node 绝不执行：没有 fetch committed checkpoint
            assert not any(
                "fetch" in (cp.committed_object_refs or {}).get("node_type", "") for cp in cps
            )
        finally:
            session.close()
    finally:
        if proc.poll() is None:
            _kill_worker(proc)
