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
from app.domain.models import CompletionDecision, DomainEvent, Run, Task, URLResource
from app.domain.repository import (
    RecordRepository,
    RunRepository,
    SpecVersionRepository,
    TaskRepository,
)
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy import event as sqlalchemy_event
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


@pytest.mark.asyncio
async def test_fail_run_redacts_an_unknown_error_code_everywhere(monkeypatch, tmp_path) -> None:
    from app.domain.models import OutboxEvent

    engine = create_engine(f"sqlite:///{tmp_path / 'redacted-failure.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user = User(email="redacted-failure@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="redacted", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        task.state = "RUNNING"
        run.state = "running"
        session.commit()
        values = {"task_id": task.id, "user_id": user.id, "run_id": run.id}
    finally:
        session.close()

    secret_code = "Bearer private-token"
    await fail_run(FailRunInput(**values, error_code=secret_code))

    session = factory()
    try:
        payloads = [event.payload for event in session.query(DomainEvent).order_by(DomainEvent.id)]
        payloads.extend(
            event.payload for event in session.query(OutboxEvent).order_by(OutboxEvent.id)
        )
        assert all(secret_code not in str(payload) for payload in payloads)
        assert {payload.get("reason") or payload.get("error_code") for payload in payloads} >= {
            "EXECUTION_FAILED"
        }
    finally:
        session.close()


@pytest.mark.asyncio
async def test_resolve_completion_missing_run_is_typed_failure(monkeypatch, tmp_path) -> None:
    import app.activities.completion as completion

    engine = create_engine(f"sqlite:///{tmp_path / 'missing-run.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(completion, "get_session_factory", lambda: factory)

    result = await completion.resolve_completion(
        completion.ResolveCompletionInput(
            task_id=1, user_id=1, run_id=999, spec_version=1, plan_version=1
        )
    )

    assert result.partial is False
    assert result.status == "FAILED"
    assert result.failure_code == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_resolve_completion_persists_exact_scope_metadata(monkeypatch, tmp_path) -> None:
    import app.activities.completion as completion

    engine = create_engine(f"sqlite:///{tmp_path / 'completion-metadata.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(completion, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user = User(email="completion-metadata@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(
            user_id=user.id, title="completion metadata", task_type="SPECIFIED_SOURCE"
        )
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        SpecVersionRepository(session).create(
            user_id=user.id,
            task_id=task.id,
            version=1,
            spec_type="collection",
            schema_version="1",
            payload={"task_type": "SPECIFIED_SOURCE"},
        )
        session.add(
            URLResource(
                user_id=user.id,
                task_id=task.id,
                run_id=run.id,
                url="https://kairos.test/complete",
                url_hash="a" * 64,
                status="HANDED_OFF",
            )
        )
        session.commit()
        RecordRepository(session).create(
            user_id=user.id,
            task_id=task.id,
            run_id=run.id,
            spec_version=1,
            payload={"name": "one"},
        )
        values = {"task_id": task.id, "user_id": user.id, "run_id": run.id}
    finally:
        session.close()

    result = await completion.resolve_completion(
        completion.ResolveCompletionInput(**values, spec_version=1, plan_version=1)
    )
    replay = await completion.resolve_completion(
        completion.ResolveCompletionInput(**values, spec_version=1, plan_version=1)
    )

    assert result.completion_type == "directional_scope_complete"
    assert replay.completion_id == result.completion_id
    session = factory()
    try:
        decision = session.query(CompletionDecision).one()
        assert decision.scope_completion_metadata == {
            "eligible_urls": 1,
            "terminal_urls": 1,
            "fetched_pages": 1,
            "records": 1,
            "scope_complete": True,
        }
    finally:
        session.close()


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
async def test_terminal_activity_concurrent_claim_has_one_winner(
    monkeypatch, tmp_path, activity, input_type, task_state, run_state, event_type
) -> None:
    import asyncio
    import threading

    engine = create_engine(
        f"sqlite:///{tmp_path / f'concurrent-{run_state}.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user = User(email=f"concurrent-{run_state}@kairos.test", password_hash="hash")
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

    barrier = threading.Barrier(2)

    def synchronize_run_claim(_conn, _cursor, statement, *_args) -> None:
        if statement.lstrip().upper().startswith("UPDATE RUNS"):
            barrier.wait(timeout=10)

    sqlalchemy_event.listen(engine, "before_cursor_execute", synchronize_run_claim)
    try:
        await asyncio.gather(
            *[
                asyncio.to_thread(lambda: asyncio.run(activity(input_type(**values))))
                for _ in range(2)
            ]
        )
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", synchronize_run_claim)

    session = factory()
    try:
        stored = session.get(Run, values["run_id"])
        assert stored is not None and stored.state == run_state
        assert session.query(DomainEvent).filter_by(event_type=event_type).count() == 1
    finally:
        session.close()


@pytest.mark.asyncio
async def test_terminal_claim_rolls_back_and_can_retry_after_event_failure(
    monkeypatch, tmp_path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'terminal-retry.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user = User(email="terminal-retry@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="retry", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        task.state = "RUNNING"
        run.state = "running"
        session.commit()
        values = {"task_id": task.id, "user_id": user.id, "run_id": run.id}
    finally:
        session.close()

    original_append = task_execution.append_domain_event

    def raise_event(*_args, **_kwargs):
        raise RuntimeError("event persistence fault")

    monkeypatch.setattr(task_execution, "append_domain_event", raise_event)
    with pytest.raises(RuntimeError, match="event persistence fault"):
        await complete_run(CompleteRunInput(**values))

    session = factory()
    try:
        assert session.get(Run, values["run_id"]).state == "running"
        assert session.get(Task, values["task_id"]).state == "RUNNING"
        assert session.query(DomainEvent).count() == 0
    finally:
        session.close()

    monkeypatch.setattr(task_execution, "append_domain_event", original_append)
    await complete_run(CompleteRunInput(**values))

    session = factory()
    try:
        assert session.get(Run, values["run_id"]).state == "completed"
        assert session.query(DomainEvent).filter_by(event_type="run.completed").count() == 1
    finally:
        session.close()
