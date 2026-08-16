"""Execution lifecycle persistence contracts."""

from __future__ import annotations

import threading
from collections.abc import Generator
from dataclasses import dataclass

import pytest
from app.activities.execution_seam import ExecutionUnit
from app.auth.models import User
from app.domain.models import Checkpoint, DomainEvent, NodeAttempt, NodeRun, Run
from app.domain.repository import (
    NodeAttemptRepository,
    NodeRunRepository,
    RunRepository,
    TaskRepository,
)
from app.execution.lifecycle import ExecutionLifecycleRecorder, append_checkpoint_event
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy import event as sqlalchemy_event
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


def test_concurrent_terminal_results_preserve_one_winner(tmp_path) -> None:
    """Both workers can read an unfinished attempt; only one may finish it."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'terminal-concurrent.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        user = User(email="terminal-concurrent@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="race", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        unit = ExecutionUnit(
            run_id=run.id,
            index=1,
            unit_type="fetch",
            input_fingerprint="f" * 64,
            node_id="fetch-1",
            node_type="fetch",
        )
        ExecutionLifecycleRecorder(session).start_attempt(run_id=run.id, unit=unit, attempt=1)
        ids = run.id, unit
    finally:
        session.close()

    barrier = threading.Barrier(2)

    def synchronize_terminal_claim(conn, _cursor, statement, *_args) -> None:
        if statement.lstrip().upper().startswith("UPDATE NODE_ATTEMPTS"):
            barrier.wait(timeout=10)

    sqlalchemy_event.listen(engine, "before_cursor_execute", synchronize_terminal_claim)

    def finish(status: str, error_code: str | None) -> None:
        worker = factory()
        try:
            ExecutionLifecycleRecorder(worker).finish_attempt(
                run_id=ids[0],
                unit=ids[1],
                attempt=1,
                status=status,
                committed_refs={"fetched": 1},
                error_code=error_code,
            )
        finally:
            worker.close()

    try:
        workers = [
            threading.Thread(target=finish, args=("SUCCEEDED", None)),
            threading.Thread(target=finish, args=("FAILED", "NETWORK")),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
        assert all(not worker.is_alive() for worker in workers)
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", synchronize_terminal_claim)

    session = factory()
    try:
        attempt = session.query(NodeAttempt).one()
        terminal_events = session.query(DomainEvent).filter(
            DomainEvent.event_type.in_(("run.node_completed", "run.node_failed"))
        )
        assert attempt.status in {"SUCCEEDED", "FAILED"}
        assert terminal_events.count() == 1
        event = terminal_events.one()
        assert event.event_type == (
            "run.node_completed" if attempt.status == "SUCCEEDED" else "run.node_failed"
        )
    finally:
        session.close()


def test_concurrent_checkpoint_event_backfill_claims_once(tmp_path) -> None:
    """A legacy checkpoint without an event is backfilled by exactly one worker."""
    from app.domain.models import Checkpoint

    engine = create_engine(
        f"sqlite:///{tmp_path / 'checkpoint-concurrent.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        user = User(email="checkpoint-concurrent@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="race", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        checkpoint = Checkpoint(
            user_id=user.id,
            task_id=task.id,
            run_id=run.id,
            batch_identity="batch",
            spec_version=1,
            plan_version=1,
            node_run_id=None,
            input_fingerprint="f" * 64,
            committed_object_refs={"fetched": 1},
            content_hash=None,
        )
        session.add(checkpoint)
        session.commit()
        checkpoint_id = checkpoint.id
    finally:
        session.close()

    barrier = threading.Barrier(2)
    claim_statements = 0
    claim_lock = threading.Lock()

    def synchronize_event_claim(conn, _cursor, statement, *_args) -> None:
        nonlocal claim_statements
        normalized = statement.lstrip().upper()
        if normalized.startswith(("INSERT INTO IDEMPOTENCY_KEYS", "INSERT INTO DOMAIN_EVENTS")):
            with claim_lock:
                claim_statements += 1
                should_wait = claim_statements <= 2
            if should_wait:
                barrier.wait(timeout=10)

    sqlalchemy_event.listen(engine, "before_cursor_execute", synchronize_event_claim)
    outcomes: list[bool] = []
    errors: list[Exception] = []

    def backfill() -> None:
        worker = factory()
        try:
            checkpoint = worker.get(Checkpoint, checkpoint_id)
            outcomes.append(append_checkpoint_event(worker, checkpoint))
            worker.commit()
        except Exception as exc:  # test reports worker failures deterministically
            errors.append(exc)
            worker.rollback()
        finally:
            worker.close()

    try:
        workers = [threading.Thread(target=backfill) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
        assert all(not worker.is_alive() for worker in workers)
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", synchronize_event_claim)

    assert not errors
    assert sorted(outcomes) == [False, True]
    session = factory()
    try:
        assert (
            session.query(DomainEvent).filter_by(event_type="run.checkpoint_committed").count() == 1
        )
    finally:
        session.close()


def test_checkpoint_event_failure_rolls_back_its_idempotency_claim(
    lifecycle_case: LifecycleCase, monkeypatch
) -> None:
    """A failed backfill remains retryable; it must not leave a durable claim."""
    checkpoint = Checkpoint(
        user_id=lifecycle_case.run.user_id,
        task_id=lifecycle_case.run.task_id,
        run_id=lifecycle_case.run.id,
        batch_identity="event-fault",
        spec_version=lifecycle_case.run.spec_version,
        plan_version=lifecycle_case.run.plan_version,
        node_run_id=None,
        input_fingerprint="f" * 64,
        committed_object_refs={"fetched": 1},
        content_hash=None,
    )
    lifecycle_case.session.add(checkpoint)
    lifecycle_case.session.commit()

    from app.execution import lifecycle

    original_append = lifecycle.append_domain_event
    monkeypatch.setattr(
        lifecycle,
        "append_domain_event",
        lambda *_, **__: (_ for _ in ()).throw(RuntimeError("event fault")),
    )
    with pytest.raises(RuntimeError, match="event fault"):
        append_checkpoint_event(lifecycle_case.session, checkpoint)
    lifecycle_case.session.commit()

    monkeypatch.setattr(lifecycle, "append_domain_event", original_append)
    assert append_checkpoint_event(lifecycle_case.session, checkpoint) is True
    lifecycle_case.session.commit()
    assert (
        lifecycle_case.session.query(DomainEvent)
        .filter_by(event_type="run.checkpoint_committed")
        .count()
        == 1
    )


def test_older_terminal_attempt_cannot_regress_newer_success(lifecycle_case: LifecycleCase) -> None:
    lifecycle_case.recorder.start_attempt(
        run_id=lifecycle_case.run.id, unit=lifecycle_case.unit, attempt=1
    )
    lifecycle_case.recorder.start_attempt(
        run_id=lifecycle_case.run.id, unit=lifecycle_case.unit, attempt=2
    )
    lifecycle_case.recorder.finish_attempt(
        run_id=lifecycle_case.run.id,
        unit=lifecycle_case.unit,
        attempt=2,
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

    node = lifecycle_case.session.query(NodeRun).one()
    assert node.state == "SUCCEEDED"
    assert node.finished_at is not None
    assert lifecycle_case.event_types()[-2:] == ["run.node_completed", "run.node_failed"]


def test_late_older_start_cannot_clear_newer_terminal_fact(lifecycle_case: LifecycleCase) -> None:
    lifecycle_case.recorder.start_attempt(
        run_id=lifecycle_case.run.id, unit=lifecycle_case.unit, attempt=1
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
    finished_at = lifecycle_case.session.query(NodeRun).one().finished_at
    lifecycle_case.recorder.start_attempt(
        run_id=lifecycle_case.run.id, unit=lifecycle_case.unit, attempt=1
    )
    node = lifecycle_case.session.query(NodeRun).one()
    assert node.state == "SUCCEEDED"
    assert node.finished_at == finished_at


def test_authoritative_retry_start_clears_prior_terminal_timestamp(
    lifecycle_case: LifecycleCase,
) -> None:
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
    assert lifecycle_case.session.query(NodeRun).one().finished_at is not None
    lifecycle_case.recorder.start_attempt(
        run_id=lifecycle_case.run.id, unit=lifecycle_case.unit, attempt=2
    )
    node = lifecycle_case.session.query(NodeRun).one()
    assert node.state == "RUNNING"
    assert node.finished_at is None


def test_delayed_pending_progress_cannot_reopen_newer_terminal(
    lifecycle_case: LifecycleCase,
) -> None:
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
    node = lifecycle_case.session.query(NodeRun).one()
    lifecycle_case.session.add(
        NodeAttempt(
            user_id=lifecycle_case.run.user_id, node_run_id=node.id, attempt=1, status="PENDING"
        )
    )
    lifecycle_case.session.commit()
    finished_at = lifecycle_case.session.query(NodeRun).one().finished_at

    lifecycle_case.recorder.finish_attempt(
        run_id=lifecycle_case.run.id,
        unit=lifecycle_case.unit,
        attempt=1,
        status="RUNNING",
        committed_refs={},
        error_code=None,
    )
    node = lifecycle_case.session.query(NodeRun).one()
    assert node.state == "SUCCEEDED"
    assert node.finished_at == finished_at


def test_unknown_lifecycle_status_fails_closed_without_secret_text(
    lifecycle_case: LifecycleCase,
) -> None:
    lifecycle_case.recorder.finish_attempt(
        run_id=lifecycle_case.run.id,
        unit=lifecycle_case.unit,
        attempt=1,
        status="ARBITRARY_PROGRESS",
        committed_refs={},
        error_code="Authorization",
        safe_message="Bearer secret-token",
    )

    event = lifecycle_case.session.query(DomainEvent).order_by(DomainEvent.id.desc()).first()
    attempt = lifecycle_case.session.query(NodeAttempt).one()
    assert attempt.status == "FAILED"
    assert event.event_type == "run.node_failed"
    assert event.payload["reason_code"] == "INVALID_LIFECYCLE_STATUS"
    assert "secret-token" not in str(event.payload)


def test_concurrent_start_attempt_claims_one_started_event(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'start-attempt-concurrent.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        user = User(email="start-attempt-race@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="race", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        unit = ExecutionUnit(
            run_id=run.id,
            index=1,
            unit_type="fetch",
            input_fingerprint="f" * 64,
            node_id="fetch-1",
            node_type="fetch",
        )
        node = NodeRun(
            user_id=user.id,
            run_id=run.id,
            task_id=task.id,
            node_id=unit.node_id,
            node_type="fetch",
            position=unit.index,
            input_fingerprint=unit.input_fingerprint,
            state="PENDING",
            version=1,
        )
        session.add(node)
        session.flush()
        session.add(NodeAttempt(user_id=user.id, node_run_id=node.id, attempt=1, status="PENDING"))
        session.commit()
        run_id = run.id
    finally:
        session.close()

    def start() -> None:
        worker = factory()
        try:
            ExecutionLifecycleRecorder(worker).start_attempt(run_id=run_id, unit=unit, attempt=1)
        finally:
            worker.close()

    barrier = threading.Barrier(2)

    def synchronize_start_claim(conn, _cursor, statement, *_args) -> None:
        if statement.lstrip().upper().startswith("UPDATE NODE_ATTEMPTS"):
            barrier.wait(timeout=10)

    sqlalchemy_event.listen(engine, "before_cursor_execute", synchronize_start_claim)
    try:
        workers = [threading.Thread(target=start) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
        assert all(not worker.is_alive() for worker in workers)
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", synchronize_start_claim)
    session = factory()
    try:
        assert session.query(NodeAttempt).count() == 1
        assert session.query(DomainEvent).filter_by(event_type="run.node_started").count() == 1
        node = session.query(NodeRun).one()
        assert node.state == "RUNNING"
        assert node.finished_at is None
    finally:
        session.close()
