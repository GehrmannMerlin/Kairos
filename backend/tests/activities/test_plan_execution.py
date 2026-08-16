"""Plan execution activity lifecycle integration."""

from __future__ import annotations

import app.activities.plan_execution as plan_execution
import pytest
from app.activities.execution_seam import ExecuteUnitInput, ExecuteUnitResult, ExecutionUnit
from app.auth.models import User
from app.domain.models import DomainEvent, NodeAttempt, NodeRun
from app.domain.repository import RunRepository, TaskRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.mark.asyncio
async def test_execute_safe_unit_persists_lifecycle_for_direct_activity_test(
    monkeypatch, tmp_path
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'plan-execution.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(plan_execution, "get_session_factory", lambda: factory)

    session = factory()
    try:
        user = User(email="plan-execution@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="run node", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        run_id = run.id
    finally:
        session.close()

    async def executor(_: ExecutionUnit) -> ExecuteUnitResult:
        return ExecuteUnitResult(
            unit_index=1, committed_refs={"fetched": 2}, safe_message="fetch completed"
        )

    monkeypatch.setattr(plan_execution, "get_node_executor", lambda _: executor)
    result = await plan_execution.execute_safe_unit(
        ExecuteUnitInput(
            run_id=run_id,
            unit=ExecutionUnit(
                run_id=run_id,
                index=1,
                unit_type="fetch",
                node_id="fetch-1",
                node_type="fetch",
                input_fingerprint="f" * 64,
            ),
        )
    )

    assert result.status == "OK"
    session = factory()
    try:
        assert session.query(NodeRun).count() == 1
        assert session.query(NodeAttempt).one().attempt == 1
        events = list(session.query(DomainEvent).order_by(DomainEvent.id))
        assert [event.event_type for event in events] == [
            "run.node_started",
            "run.node_completed",
        ]
        assert events[-1].payload["safe_message"] == "fetch completed"
    finally:
        session.close()


@pytest.mark.asyncio
async def test_execute_safe_unit_records_safe_failure_before_reraising(
    monkeypatch, tmp_path
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'plan-execution-failure.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(plan_execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user = User(email="plan-failure@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="fail node", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        run_id = run.id
    finally:
        session.close()

    async def executor(_: ExecutionUnit) -> ExecuteUnitResult:
        raise RuntimeError("Authorization: private credential")

    monkeypatch.setattr(plan_execution, "get_node_executor", lambda _: executor)
    with pytest.raises(RuntimeError, match="private credential"):
        await plan_execution.execute_safe_unit(
            ExecuteUnitInput(
                run_id=run_id,
                unit=ExecutionUnit(
                    run_id=run_id,
                    index=1,
                    unit_type="fetch",
                    node_id="fetch-1",
                    node_type="fetch",
                    input_fingerprint="f" * 64,
                ),
            )
        )

    session = factory()
    try:
        events = list(session.query(DomainEvent).order_by(DomainEvent.id))
        assert [event.event_type for event in events] == ["run.node_started", "run.node_failed"]
        assert events[-1].payload["reason_code"] == "INTERNAL"
        assert "private credential" not in str(events[-1].payload)
    finally:
        session.close()
