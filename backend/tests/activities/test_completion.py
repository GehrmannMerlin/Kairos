"""Task 7 terminal activity facts and replay behavior."""

from __future__ import annotations

import app.activities.task_execution as task_execution
import pytest
from app.activities.task_execution import (
    CompleteRunInput,
    FailRunInput,
    MarkCancelledInput,
    MarkPartialInput,
    complete_run,
    fail_run,
    mark_cancelled,
    mark_partial,
)
from app.auth.models import User
from app.domain.models import DomainEvent, Run
from app.domain.repository import RunRepository, TaskRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("activity", "input_type", "task_state", "run_state", "event_type"),
    [
        (complete_run, CompleteRunInput, "RUNNING", "completed", "run.completed"),
        (
            mark_partial,
            MarkPartialInput,
            "RUNNING",
            "partially_completed",
            "run.partially_completed",
        ),
        (fail_run, FailRunInput, "RUNNING", "failed", "run.failed"),
        (mark_cancelled, MarkCancelledInput, "CANCELLING", "cancelled", "run.cancelled"),
    ],
)
async def test_terminal_activity_appends_one_run_event_on_same_state_replay(
    monkeypatch, tmp_path, activity, input_type, task_state, run_state, event_type
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / f'{run_state}.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user = User(email=f"{run_state}@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title=run_state, task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        task.state = task_state
        run.state = "running"
        session.commit()
        values = {"task_id": task.id, "user_id": user.id, "run_id": run.id}
    finally:
        session.close()

    await activity(input_type(**values))
    await activity(input_type(**values))

    session = factory()
    try:
        stored = session.get(Run, values["run_id"])
        assert stored is not None and stored.state == run_state
        events = session.query(DomainEvent).filter_by(event_type=event_type).all()
        assert len(events) == 1
        assert events[0].payload["transition"] == event_type
    finally:
        session.close()


@pytest.mark.asyncio
async def test_fail_run_persists_only_the_typed_failure_reason(monkeypatch, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'typed-failure.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user = User(email="typed-failure@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(
            user_id=user.id, title="typed failure", task_type=None
        )
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        task.state = "RUNNING"
        run.state = "running"
        session.commit()
        values = {"task_id": task.id, "user_id": user.id, "run_id": run.id}
    finally:
        session.close()

    await fail_run(FailRunInput(**values, error_code="STORAGE_ERROR"))

    session = factory()
    try:
        task_event = session.query(DomainEvent).filter_by(event_type="task.fail").one()
        run_event = session.query(DomainEvent).filter_by(event_type="run.failed").one()
        assert task_event.payload["reason"] == "STORAGE_ERROR"
        assert run_event.payload["error_code"] == "STORAGE_ERROR"
    finally:
        session.close()
