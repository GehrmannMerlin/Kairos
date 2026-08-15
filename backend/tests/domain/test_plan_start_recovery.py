"""Durable plan-start preparation and active-run serialization."""

from __future__ import annotations

import asyncio

import pytest
from app.domain.models import Run
from app.plan.service import PlanService
from app.workflows.starter import RunStartedResult


class _YieldingStarter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def start_persisted_run(self, **kwargs) -> RunStartedResult:
        self.calls.append(kwargs)
        await asyncio.sleep(0)
        return RunStartedResult(kwargs["run_id"], kwargs["workflow_id"])


def _persist_plan(db, task, user, starter) -> None:
    PlanService(db, starter=starter).persist_plan(
        user_id=user.id,
        task_id=task.id,
        spec_version=1,
        graph={"nodes": []},
        validation_status="VALID",
        fingerprint_value="fp-plan-start",
        registry_versions={},
    )


def test_validator_issue_summaries_are_persisted_with_plan(db, task, user) -> None:
    service = PlanService(db, starter=None)
    service.persist_plan(
        user_id=user.id,
        task_id=task.id,
        spec_version=1,
        graph={"nodes": []},
        validation_status="REQUIRES_NEW_SPEC",
        fingerprint_value="fp-with-issues",
        registry_versions={},
        validation_issues=[{"code": "SPEC_SCOPE_EXPANSION", "node_id": "fetch-1"}],
    )

    summary = service.get_plan_summary(user_id=user.id, task_id=task.id, plan_version=1)

    assert summary["validator_issues"] == [{"code": "SPEC_SCOPE_EXPANSION", "node_id": "fetch-1"}]


@pytest.mark.asyncio
async def test_two_concurrent_starts_create_one_active_run(db, task, user) -> None:
    starter = _YieldingStarter()
    _persist_plan(db, task, user, starter)
    service = PlanService(db, starter=starter)

    first, second = await asyncio.gather(
        service.auto_start(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1),
        service.auto_start(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1),
    )

    assert first == second
    assert first[1] == f"task-workflow-{task.id}"
    assert db.query(Run).filter(Run.task_id == task.id).count() == 1


@pytest.mark.asyncio
async def test_terminal_run_allows_a_legitimate_rerun(db, task, user) -> None:
    starter = _YieldingStarter()
    _persist_plan(db, task, user, starter)
    service = PlanService(db, starter=starter)

    first_run_id, _ = await service.auto_start(
        user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
    )
    first = db.get(Run, first_run_id)
    first.state = "completed"
    db.commit()

    second_run_id, _ = await service.auto_start(
        user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
    )

    assert second_run_id != first_run_id
    assert db.query(Run).filter(Run.task_id == task.id).count() == 2
