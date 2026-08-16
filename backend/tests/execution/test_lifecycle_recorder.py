"""Execution lifecycle persistence contracts."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

import pytest
from app.activities.execution_seam import ExecutionUnit
from app.auth.models import User
from app.domain.models import DomainEvent, NodeAttempt, NodeRun, Run
from app.domain.repository import (
    NodeAttemptRepository,
    NodeRunRepository,
    RunRepository,
    TaskRepository,
)
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
        committed_refs={
            "fetched": 3,
            "artifact_id": 99,
            "raw_ref": 7,
            "authorization": "secret",
            "html": "private",
        },
        error_code=None,
    )

    event = lifecycle_case.session.query(DomainEvent).order_by(DomainEvent.id.desc()).first()
    assert event is not None
    assert event.event_type == "run.node_completed"
    assert event.payload["counts"] == {"fetched": 3}
    assert "authorization" not in str(event.payload)
    assert "private" not in str(event.payload)
    assert "artifact_id" not in event.payload["counts"]
    assert "raw_ref" not in event.payload["counts"]


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


def test_first_terminal_attempt_result_is_immutable(lifecycle_case: LifecycleCase) -> None:
    lifecycle_case.recorder.start_attempt(
        run_id=lifecycle_case.run.id, unit=lifecycle_case.unit, attempt=1
    )
    lifecycle_case.recorder.finish_attempt(
        run_id=lifecycle_case.run.id,
        unit=lifecycle_case.unit,
        attempt=1,
        status="SUCCEEDED",
        committed_refs={"fetched": 1},
        error_code=None,
    )
    lifecycle_case.recorder.finish_attempt(
        run_id=lifecycle_case.run.id,
        unit=lifecycle_case.unit,
        attempt=1,
        status="FAILED",
        committed_refs={"failed": 1},
        error_code="NETWORK",
    )

    assert lifecycle_case.event_types() == ["run.node_started", "run.node_completed"]


def test_unavailable_executor_is_recorded_as_failed_attempt(lifecycle_case: LifecycleCase) -> None:
    lifecycle_case.recorder.finish_attempt(
        run_id=lifecycle_case.run.id,
        unit=lifecycle_case.unit,
        attempt=1,
        status="NODE_EXECUTOR_UNAVAILABLE",
        committed_refs={},
        error_code="NODE_EXECUTOR_UNAVAILABLE",
    )

    attempt = lifecycle_case.session.query(NodeAttempt).one()
    assert attempt.status == "FAILED"
    assert attempt.error_code == "NODE_EXECUTOR_UNAVAILABLE"


def test_node_run_identity_conflict_keeps_session_usable(
    lifecycle_case: LifecycleCase, monkeypatch
) -> None:
    existing = NodeRun(
        user_id=lifecycle_case.run.user_id,
        run_id=lifecycle_case.run.id,
        task_id=lifecycle_case.run.task_id,
        node_id=lifecycle_case.unit.node_id,
        node_type="fetch",
        position=1,
        input_fingerprint=lifecycle_case.unit.input_fingerprint,
        state="PENDING",
        version=1,
    )
    lifecycle_case.session.add(existing)
    lifecycle_case.session.commit()
    real_scalar = lifecycle_case.session.scalar
    calls = 0

    def race_once(statement, *args, **kwargs):
        nonlocal calls
        calls += 1
        return None if calls == 1 else real_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(lifecycle_case.session, "scalar", race_once)
    row = NodeRunRepository(lifecycle_case.session).get_or_create(
        user_id=lifecycle_case.run.user_id,
        run_id=lifecycle_case.run.id,
        task_id=lifecycle_case.run.task_id,
        node_id=lifecycle_case.unit.node_id or "fetch-1",
        node_type="fetch",
        position=1,
        input_fingerprint=lifecycle_case.unit.input_fingerprint,
    )

    assert row.id == existing.id
    assert lifecycle_case.session.query(Run).count() == 1


def test_node_attempt_identity_conflict_keeps_session_usable(
    lifecycle_case: LifecycleCase, monkeypatch
) -> None:
    node = NodeRunRepository(lifecycle_case.session).get_or_create(
        user_id=lifecycle_case.run.user_id,
        run_id=lifecycle_case.run.id,
        task_id=lifecycle_case.run.task_id,
        node_id=lifecycle_case.unit.node_id or "fetch-1",
        node_type="fetch",
        position=1,
        input_fingerprint=lifecycle_case.unit.input_fingerprint,
    )
    existing = NodeAttempt(user_id=lifecycle_case.run.user_id, node_run_id=node.id, attempt=1)
    lifecycle_case.session.add(existing)
    lifecycle_case.session.commit()
    real_scalar = lifecycle_case.session.scalar
    calls = 0

    def race_once(statement, *args, **kwargs):
        nonlocal calls
        calls += 1
        return None if calls == 1 else real_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(lifecycle_case.session, "scalar", race_once)
    row = NodeAttemptRepository(lifecycle_case.session).get_or_create(
        user_id=lifecycle_case.run.user_id, node_run_id=node.id, attempt=1
    )

    assert row.id == existing.id
    assert lifecycle_case.session.query(Run).count() == 1
