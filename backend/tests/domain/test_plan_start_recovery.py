"""Durable plan-start preparation and active-run serialization."""

from __future__ import annotations

import asyncio

import pytest
from app.config import Settings
from app.domain.errors import DomainError, ExecutionPreflightBlockedError
from app.domain.models import Run, Task
from app.domain.repository import SpecVersionRepository
from app.domain.task_types import TaskType
from app.plan.nodes import NodeType
from app.plan.service import PlanService
from app.workflows.starter import RunStartedResult
from sqlalchemy.orm import Session


class _YieldingStarter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def start_persisted_run(self, **kwargs) -> RunStartedResult:
        self.calls.append(kwargs)
        await asyncio.sleep(0)
        return RunStartedResult(kwargs["run_id"], kwargs["workflow_id"])


def _persist_frozen_plan(db, task, user, starter) -> None:
    task.task_type = TaskType.SPECIFIED_SOURCE.value
    task.current_spec_version = 1
    db.add(task)
    db.commit()
    SpecVersionRepository(db).create(
        user_id=user.id,
        task_id=task.id,
        version=1,
        spec_type="collection",
        schema_version="m06.1",
        payload={
            "task_type": TaskType.SPECIFIED_SOURCE.value,
            "goal": "采集公司信息",
            "fields": [{"name": "公司名", "type": "text", "required": True}],
            "source_scope": {
                "mode": TaskType.SPECIFIED_SOURCE.value,
                "seed_urls": ["https://example.com"],
                "source_hints": [],
            },
        },
    )
    PlanService(db, starter=starter).persist_plan(
        user_id=user.id,
        task_id=task.id,
        spec_version=1,
        graph={
            "task_id": task.id,
            "spec_version": 1,
            "task_type": TaskType.SPECIFIED_SOURCE.value,
            "nodes": [
                {
                    "node_id": "fetch-1",
                    "node_type": NodeType.FETCH.value,
                    "definition_version": "1.0.0",
                    "parameters": {"url_template": "https://example.com/{id}"},
                    "depends_on": [],
                }
            ],
            "edges": [],
        },
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
    _persist_frozen_plan(db, task, user, starter)
    service = PlanService(db, starter=starter, settings=Settings())

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
    _persist_frozen_plan(db, task, user, starter)
    service = PlanService(db, starter=starter, settings=Settings())

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


@pytest.mark.asyncio
async def test_auto_start_blocks_before_creating_a_run_or_dispatching(db, task, user) -> None:
    starter = _YieldingStarter()
    _persist_frozen_plan(db, task, user, starter)
    blocked_settings = Settings(s3_endpoint="", s3_bucket="", s3_access_key="")
    service = PlanService(db, starter=starter, settings=blocked_settings)

    with pytest.raises(ExecutionPreflightBlockedError):
        await service.auto_start(
            user_id=user.id,
            task_id=task.id,
            spec_version=1,
            plan_version=1,
        )

    assert starter.calls == []
    assert db.query(Run).filter(Run.task_id == task.id).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("advance", ["plan", "spec"])
async def test_auto_start_rejects_ready_fact_when_current_identity_changes(
    db, task, user, advance
) -> None:
    starter = _YieldingStarter()
    _persist_frozen_plan(db, task, user, starter)
    service = PlanService(db, starter=starter, settings=Settings())
    service.require_ready_preflight(
        user_id=user.id,
        task_id=task.id,
        spec_version=1,
        plan_version=1,
        settings=Settings(),
    )

    if advance == "plan":
        service.persist_plan(
            user_id=user.id,
            task_id=task.id,
            spec_version=1,
            graph={
                "task_id": task.id,
                "spec_version": 1,
                "task_type": TaskType.SPECIFIED_SOURCE.value,
                "nodes": [],
                "edges": [],
            },
            validation_status="VALID",
            fingerprint_value="fp-plan-start-v2",
            registry_versions={},
        )
    else:
        SpecVersionRepository(db).create(
            user_id=user.id,
            task_id=task.id,
            version=2,
            spec_type="collection",
            schema_version="m06.1",
            payload={
                "task_type": TaskType.SPECIFIED_SOURCE.value,
                "goal": "采集公司信息",
                "fields": [{"name": "公司名", "type": "text", "required": True}],
                "source_scope": {
                    "mode": TaskType.SPECIFIED_SOURCE.value,
                    "seed_urls": ["https://example.com"],
                    "source_hints": [],
                },
            },
        )
        task.current_spec_version = 2
        db.add(task)
        db.commit()

    with pytest.raises(DomainError, match="当前冻结"):
        await service.auto_start(
            user_id=user.id,
            task_id=task.id,
            spec_version=1,
            plan_version=1,
        )

    assert starter.calls == []
    assert db.query(Run).filter(Run.task_id == task.id).count() == 0


@pytest.mark.asyncio
async def test_auto_start_refreshes_locked_task_after_another_session_advances_plan(
    db, task, user
) -> None:
    starter = _YieldingStarter()
    _persist_frozen_plan(db, task, user, starter)
    session_a = Session(bind=db.get_bind(), expire_on_commit=False)
    session_b = Session(bind=db.get_bind(), expire_on_commit=False)
    try:
        service_a = PlanService(session_a, starter=starter, settings=Settings())
        service_a.require_ready_preflight(
            user_id=user.id,
            task_id=task.id,
            spec_version=1,
            plan_version=1,
            settings=Settings(),
        )
        stale_task = session_a.get(Task, task.id)
        assert stale_task is not None
        assert stale_task.current_plan_version == 1

        PlanService(session_b, starter=None).persist_plan(
            user_id=user.id,
            task_id=task.id,
            spec_version=1,
            graph={
                "task_id": task.id,
                "spec_version": 1,
                "task_type": TaskType.SPECIFIED_SOURCE.value,
                "nodes": [],
                "edges": [],
            },
            validation_status="VALID",
            fingerprint_value="fp-plan-start-interleaved-v2",
            registry_versions={},
        )
        assert stale_task.current_plan_version == 1

        with pytest.raises(DomainError, match="当前冻结"):
            await service_a.auto_start(
                user_id=user.id,
                task_id=task.id,
                spec_version=1,
                plan_version=1,
            )

        assert starter.calls == []
        assert session_a.query(Run).filter(Run.task_id == task.id).count() == 0
    finally:
        session_a.close()
        session_b.close()
