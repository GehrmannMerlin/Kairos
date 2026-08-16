"""Task 7 terminal activity facts and replay behavior."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime

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
from app.domain.models import (
    CompletionDecision,
    DomainEvent,
    IdempotencyKey,
    Record,
    Run,
    Task,
    URLResource,
    ValidationResult,
)
from app.domain.repository import (
    RecordRepository,
    RunRepository,
    SpecVersionRepository,
    TaskRepository,
)
from app.infra.db import Base
from app.validation.completion import CompletionDecisionView
from sqlalchemy import create_engine, select
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.orm import sessionmaker
from temporalio.exceptions import ApplicationError


def _running_pair(session):
    user = User(email="quality@kairos.test", password_hash="hash")
    session.add(user)
    session.commit()
    first = TaskRepository(session).create(user_id=user.id, title="first", task_type=None)
    second = TaskRepository(session).create(user_id=user.id, title="second", task_type=None)
    run = RunRepository(session).create(
        user_id=user.id, task_id=first.id, spec_version=7, plan_version=9
    )
    first.state = second.state = "RUNNING"
    run.state = "running"
    session.commit()
    return user, first, second, run


def _completion_context(session, task_type="EXPLORATORY"):
    user, task, _other, run = _running_pair(session)
    task.task_type = task_type
    SpecVersionRepository(session).create(
        user_id=user.id,
        task_id=task.id,
        version=7,
        spec_type="collection",
        schema_version="1",
        payload={
            "task_type": task_type,
            "completion_conditions": [{"kind": "min_records", "target": 1}],
        },
    )
    session.add(
        URLResource(
            user_id=user.id,
            task_id=task.id,
            run_id=run.id,
            spec_version=7,
            url="https://kairos.test/current",
            url_hash="d" * 64,
            status="HANDED_OFF",
        )
    )
    session.commit()
    RecordRepository(session).create(
        user_id=user.id,
        task_id=task.id,
        run_id=run.id,
        spec_version=7,
        payload={"name": "one"},
    )
    return {
        "task_id": task.id,
        "user_id": user.id,
        "run_id": run.id,
        "spec_version": 7,
        "plan_version": 9,
    }


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
async def test_terminal_identity_failure_is_non_retryable_application_error(
    monkeypatch, tmp_path
) -> None:
    import app.activities.task_execution as execution

    engine = create_engine(f"sqlite:///{tmp_path / 'wrong-task.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user, first, second, run = _running_pair(session)
        values = {"task_id": second.id, "user_id": user.id, "run_id": run.id}
    finally:
        session.close()

    with pytest.raises(ApplicationError) as raised:
        await complete_run(CompleteRunInput(**values))

    assert raised.value.type == "RUN_IDENTITY_MISMATCH"
    assert raised.value.non_retryable is True
    session = factory()
    try:
        stored_run = session.get(Run, run.id)
        stored_first = session.get(Task, first.id)
        stored_second = session.get(Task, second.id)
        assert stored_run is not None and stored_run.state == "running"
        assert stored_first is not None and stored_first.state == "RUNNING"
        assert stored_second is not None and stored_second.state == "RUNNING"
        assert session.query(DomainEvent).count() == 0
    finally:
        session.close()


@pytest.mark.asyncio
async def test_completion_rejects_same_owner_frozen_run_mismatch_without_writing(
    monkeypatch, tmp_path
) -> None:
    import app.activities.completion as completion

    engine = create_engine(f"sqlite:///{tmp_path / 'wrong-completion.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(completion, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user, _first, second, run = _running_pair(session)
        values = {
            "task_id": second.id,
            "user_id": user.id,
            "run_id": run.id,
            "spec_version": 7,
            "plan_version": 9,
        }
    finally:
        session.close()

    result = await completion.resolve_completion(completion.ResolveCompletionInput(**values))

    assert result.status == "FAILED"
    assert result.failure_code == "RUN_IDENTITY_MISMATCH"
    session = factory()
    try:
        assert session.query(CompletionDecision).count() == 0
        assert session.query(IdempotencyKey).count() == 0
    finally:
        session.close()


@pytest.mark.asyncio
async def test_completion_foreign_and_nonexistent_runs_are_indistinguishable(
    monkeypatch, tmp_path
) -> None:
    import app.activities.completion as completion

    engine = create_engine(f"sqlite:///{tmp_path / 'run-not-found.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(completion, "get_session_factory", lambda: factory)
    session = factory()
    try:
        owner, task, _other, run = _running_pair(session)
        caller = User(email="caller@kairos.test", password_hash="hash")
        session.add(caller)
        session.commit()
        base = {
            "task_id": task.id,
            "user_id": caller.id,
            "spec_version": run.spec_version,
            "plan_version": run.plan_version,
        }
        run_state = run.state
        task_state = task.state
        missing_run_id = run.id + 10_000
    finally:
        session.close()

    foreign = await completion.resolve_completion(
        completion.ResolveCompletionInput(**base, run_id=run.id)
    )
    nonexistent = await completion.resolve_completion(
        completion.ResolveCompletionInput(**base, run_id=missing_run_id)
    )

    assert (
        (
            foreign.status,
            foreign.failure_code,
            foreign.partial,
            foreign.completion_type,
            foreign.qualified_record_count,
        )
        == (
            nonexistent.status,
            nonexistent.failure_code,
            nonexistent.partial,
            nonexistent.completion_type,
            nonexistent.qualified_record_count,
        )
        == ("FAILED", "RUN_NOT_FOUND", False, None, 0)
    )
    session = factory()
    try:
        stored_run = session.get(Run, run.id)
        stored_task = session.get(Task, task.id)
        assert stored_run is not None and stored_run.state == run_state
        assert stored_task is not None and stored_task.state == task_state
        assert session.query(CompletionDecision).count() == 0
        assert session.query(IdempotencyKey).count() == 0
        assert owner.id != caller.id
    finally:
        session.close()


@pytest.mark.asyncio
async def test_terminal_conflict_is_non_retryable_but_same_replay_repairs_capacity(
    monkeypatch, tmp_path
) -> None:
    import app.activities.task_execution as execution

    engine = create_engine(f"sqlite:///{tmp_path / 'terminal-conflict.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user, first, _second, run = _running_pair(session)
        values = {"task_id": first.id, "user_id": user.id, "run_id": run.id}
    finally:
        session.close()

    await complete_run(CompleteRunInput(**values))
    releases: list[tuple[int, int]] = []
    monkeypatch.setattr(
        execution,
        "_release_task_slot",
        lambda _session, **kwargs: releases.append((kwargs["user_id"], kwargs["run_id"])),
    )
    await complete_run(CompleteRunInput(**values))
    assert releases == [(user.id, run.id)]
    with pytest.raises(ApplicationError) as raised:
        await fail_run(FailRunInput(**values, error_code="STORAGE_ERROR"))
    assert raised.value.type == "RUN_TERMINAL_CONFLICT"
    assert raised.value.non_retryable is True

    session = factory()
    try:
        stored_run = session.get(Run, run.id)
        assert stored_run is not None and stored_run.state == "completed"
        assert session.query(DomainEvent).filter_by(event_type="run.completed").count() == 1
        assert session.query(DomainEvent).filter_by(event_type="run.failed").count() == 0
    finally:
        session.close()


@pytest.mark.asyncio
async def test_completion_activity_scopes_counts_to_frozen_run_and_spec(
    monkeypatch, tmp_path
) -> None:
    import app.activities.completion as completion

    engine = create_engine(f"sqlite:///{tmp_path / 'scoped-counts.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(completion, "get_session_factory", lambda: factory)
    session = factory()
    try:
        values = _completion_context(session, task_type="SPECIFIED_SOURCE")
        current = session.get(Run, values["run_id"])
        assert current is not None
        prior = RunRepository(session).create(
            user_id=values["user_id"],
            task_id=values["task_id"],
            spec_version=6,
            plan_version=8,
        )
        session.add(
            URLResource(
                user_id=values["user_id"],
                task_id=values["task_id"],
                run_id=prior.id,
                spec_version=6,
                url="https://kairos.test/prior",
                url_hash="p" * 64,
                status="HANDED_OFF",
            )
        )
        prior_record = RecordRepository(session).create(
            user_id=values["user_id"],
            task_id=values["task_id"],
            run_id=prior.id,
            spec_version=6,
            payload={"name": "prior"},
        )
        current_record = session.scalar(select(Record).where(Record.run_id == current.id))
        assert current_record is not None
        common = {
            "user_id": values["user_id"],
            "task_id": values["task_id"],
            "validation_version": "task7-race",
            "structural_issues": [],
            "required_field_issues": [],
            "evidence_issues": [],
            "business_rule_issues": [],
            "partition": "passed",
            "allowed_actions": [],
            "validated_at": datetime(2026, 8, 16, tzinfo=UTC),
        }
        session.add_all(
            [
                ValidationResult(
                    **common,
                    run_id=current.id,
                    record_id=current_record.id,
                    spec_version_id=7,
                ),
                ValidationResult(
                    **common,
                    run_id=prior.id,
                    record_id=prior_record.id,
                    spec_version_id=6,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    result = await completion.resolve_completion(completion.ResolveCompletionInput(**values))

    assert result.qualified_record_count == 1
    assert result.completion_type == "directional_scope_complete"
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
async def test_completion_activity_fails_closed_without_persisted_stop_or_saturation(
    monkeypatch, tmp_path
) -> None:
    import app.activities.completion as completion

    engine = create_engine(f"sqlite:///{tmp_path / 'unpersisted-signals.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(completion, "get_session_factory", lambda: factory)
    session = factory()
    try:
        values = _completion_context(session)
    finally:
        session.close()

    result = await completion.resolve_completion(completion.ResolveCompletionInput(**values))

    assert result.status == "FAILED"
    assert result.failure_code == "INCOMPLETE_WITHOUT_COMPLETED_WORK"
    assert result.completion_type is None


@pytest.mark.asyncio
async def test_completion_race_returns_the_winner_decision_to_both_callers(
    monkeypatch, tmp_path
) -> None:
    import app.activities.completion as completion
    from app.validation.completion import CompletionDecisionService

    engine = create_engine(
        f"sqlite:///{tmp_path / 'completion-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(completion, "get_session_factory", lambda: factory)
    session = factory()
    try:
        values = _completion_context(session, task_type="SPECIFIED_SOURCE")
    finally:
        session.close()

    barrier = threading.Barrier(2)
    lookup_calls = 0
    decision_calls = 0
    lock = threading.Lock()

    def direct_find(db, **identity):
        nonlocal lookup_calls
        with lock:
            lookup_calls += 1
            should_wait = lookup_calls <= 2
        if should_wait:
            barrier.wait(timeout=10)
        return db.scalar(
            select(CompletionDecision).where(
                CompletionDecision.user_id == identity["user_id"],
                CompletionDecision.task_id == identity["task_id"],
                CompletionDecision.run_id == identity["run_id"],
                CompletionDecision.spec_version == identity["spec_version"],
                CompletionDecision.plan_version == identity["plan_version"],
            )
        )

    monkeypatch.setattr(completion, "_find_completion", direct_find, raising=False)

    candidates = [
        CompletionDecisionView(
            status="NORMAL_COMPLETED",
            reason="winner candidate a",
            is_partial=False,
            completion_type="directional_scope_complete",
            qualified_record_count=11,
        ),
        CompletionDecisionView(
            status="PARTIALLY_COMPLETED",
            reason="winner candidate b",
            is_partial=True,
            completion_type="access_limited",
            qualified_record_count=22,
        ),
    ]

    def different_candidate(_self, **_kwargs):
        nonlocal decision_calls
        with lock:
            candidate = candidates[decision_calls]
            decision_calls += 1
        return candidate

    monkeypatch.setattr(CompletionDecisionService, "decide", different_candidate)

    def invoke():
        return asyncio.run(
            completion.resolve_completion(completion.ResolveCompletionInput(**values))
        )

    results = await asyncio.gather(*[asyncio.to_thread(invoke) for _ in range(2)])

    assert decision_calls == 2
    session = factory()
    try:
        authoritative = session.query(CompletionDecision).one()
        expected = (
            authoritative.id,
            authoritative.status,
            authoritative.is_partial,
            authoritative.completion_type,
            authoritative.qualified_record_count,
        )
        assert {
            (
                result.completion_id,
                result.status,
                result.partial,
                result.completion_type,
                result.qualified_record_count,
            )
            for result in results
        } == {expected}
        assert session.query(IdempotencyKey).count() == 1
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
        stored_run = session.get(Run, values["run_id"])
        stored_task = session.get(Task, values["task_id"])
        assert stored_run is not None and stored_run.state == "running"
        assert stored_task is not None and stored_task.state == "RUNNING"
        assert session.query(DomainEvent).count() == 0
    finally:
        session.close()

    monkeypatch.setattr(task_execution, "append_domain_event", original_append)
    await complete_run(CompleteRunInput(**values))

    session = factory()
    try:
        stored_run = session.get(Run, values["run_id"])
        assert stored_run is not None and stored_run.state == "completed"
        assert session.query(DomainEvent).filter_by(event_type="run.completed").count() == 1
    finally:
        session.close()
