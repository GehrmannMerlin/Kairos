"""Execution lifecycle persistence contracts."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

import pytest
from app.activities.execution_seam import ExecutionUnit
from app.auth.models import User
from app.domain.models import DomainEvent, Run
from app.domain.repository import RunRepository, TaskRepository
from app.execution.lifecycle import ExecutionLifecycleRecorder
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


@dataclass
class LifecycleCase:
    recorder: ExecutionLifecycleRecorder
    run: Run
    unit: ExecutionUnit
    session: Session

    def event_types(self) -> list[str]:
        return [row.event_type for row in self.session.query(DomainEvent).order_by(DomainEvent.id)]


@pytest.fixture
def lifecycle_case(tmp_path) -> Generator[LifecycleCase]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lifecycle.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    user = User(email="lifecycle@kairos.test", password_hash="hash")
    session.add(user)
    session.commit()
    task = TaskRepository(session).create(user_id=user.id, title="lifecycle", task_type=None)
    run = RunRepository(session).create(
        user_id=user.id, task_id=task.id, spec_version=3, plan_version=5
    )
    yield LifecycleCase(
        recorder=ExecutionLifecycleRecorder(session),
        run=run,
        unit=ExecutionUnit(
            run_id=run.id,
            index=1,
            unit_type="fetch",
            input_fingerprint="f" * 64,
            node_id="fetch-1",
            node_type="fetch",
        ),
        session=session,
    )
    session.close()


def test_start_attempt_is_idempotent_for_run_node_attempt(lifecycle_case: LifecycleCase) -> None:
    first = lifecycle_case.recorder.start_attempt(
        run_id=lifecycle_case.run.id, unit=lifecycle_case.unit, attempt=1
    )
    second = lifecycle_case.recorder.start_attempt(
        run_id=lifecycle_case.run.id, unit=lifecycle_case.unit, attempt=1
    )

    assert first.node_run_id == second.node_run_id
    assert first.node_attempt_id == second.node_attempt_id
    assert lifecycle_case.event_types() == ["run.node_started"]


def test_finish_attempt_records_allowlisted_counts(lifecycle_case: LifecycleCase) -> None:
    lifecycle_case.recorder.finish_attempt(
        run_id=lifecycle_case.run.id,
        unit=lifecycle_case.unit,
        attempt=1,
        status="SUCCEEDED",
        committed_refs={"fetched": 3, "authorization": "secret", "html": "private"},
        error_code=None,
    )

    event = lifecycle_case.session.query(DomainEvent).order_by(DomainEvent.id.desc()).first()
    assert event is not None
    assert event.event_type == "run.node_completed"
    assert event.payload["counts"] == {"fetched": 3}
    assert "authorization" not in str(event.payload)
    assert "private" not in str(event.payload)


def test_retry_and_duplicate_terminal_events_are_idempotent(lifecycle_case: LifecycleCase) -> None:
    lifecycle_case.recorder.start_attempt(
        run_id=lifecycle_case.run.id, unit=lifecycle_case.unit, attempt=1
    )
    lifecycle_case.recorder.finish_attempt(
        run_id=lifecycle_case.run.id,
        unit=lifecycle_case.unit,
        attempt=1,
        status="FAILED",
        committed_refs={},
        error_code="NETWORK",
    )
    lifecycle_case.recorder.start_attempt(
        run_id=lifecycle_case.run.id, unit=lifecycle_case.unit, attempt=2
    )
    lifecycle_case.recorder.finish_attempt(
        run_id=lifecycle_case.run.id,
        unit=lifecycle_case.unit,
        attempt=2,
        status="SUCCEEDED",
        committed_refs={},
        error_code=None,
    )
    lifecycle_case.recorder.finish_attempt(
        run_id=lifecycle_case.run.id,
        unit=lifecycle_case.unit,
        attempt=2,
        status="SUCCEEDED",
        committed_refs={},
        error_code=None,
    )

    assert lifecycle_case.event_types() == [
        "run.node_started",
        "run.node_failed",
        "run.node_started",
        "run.node_completed",
    ]
