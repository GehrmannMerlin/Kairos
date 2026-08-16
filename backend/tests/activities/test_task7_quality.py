"""Task 7 follow-up ownership, scope, and terminal replay regressions."""

from __future__ import annotations

import asyncio
import threading

import pytest
from app.activities.task_execution import CompleteRunInput, FailRunInput, complete_run, fail_run
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
from sqlalchemy.orm import sessionmaker


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
        user_id=user.id, task_id=task.id, run_id=run.id, spec_version=7, payload={"name": "one"}
    )
    return {
        "task_id": task.id,
        "user_id": user.id,
        "run_id": run.id,
        "spec_version": 7,
        "plan_version": 9,
    }


@pytest.mark.asyncio
async def test_terminal_rejects_wrong_task_without_claiming_run(monkeypatch, tmp_path) -> None:
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

    with pytest.raises(ValueError, match="RUN_IDENTITY_MISMATCH"):
        await complete_run(CompleteRunInput(**values))

    session = factory()
    try:
        assert session.get(Run, run.id).state == "running"
        assert session.get(Task, first.id).state == session.get(Task, second.id).state == "RUNNING"
        assert session.query(DomainEvent).count() == 0
    finally:
        session.close()


@pytest.mark.asyncio
async def test_completion_rejects_frozen_run_identity_without_writing(
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
    finally:
        session.close()


@pytest.mark.asyncio
async def test_terminal_conflict_fails_but_same_replay_repairs_capacity(
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
    with pytest.raises(ValueError, match="RUN_TERMINAL_CONFLICT"):
        await fail_run(FailRunInput(**values, error_code="STORAGE_ERROR"))

    session = factory()
    try:
        assert session.get(Run, run.id).state == "completed"
        assert session.query(DomainEvent).filter_by(event_type="run.completed").count() == 1
        assert session.query(DomainEvent).filter_by(event_type="run.failed").count() == 0
    finally:
        session.close()


def test_completion_count_helpers_are_scoped_to_frozen_run_and_spec(tmp_path) -> None:
    import app.activities.completion as completion

    engine = create_engine(f"sqlite:///{tmp_path / 'scoped-counts.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        user, first, _second, current = _running_pair(session)
        prior = RunRepository(session).create(
            user_id=user.id, task_id=first.id, spec_version=6, plan_version=8
        )
        session.add_all(
            [
                URLResource(
                    user_id=user.id,
                    task_id=first.id,
                    run_id=current.id,
                    spec_version=7,
                    url="https://kairos.test/current",
                    url_hash="c" * 64,
                    status="HANDED_OFF",
                ),
                URLResource(
                    user_id=user.id,
                    task_id=first.id,
                    run_id=prior.id,
                    spec_version=6,
                    url="https://kairos.test/prior",
                    url_hash="p" * 64,
                    status="HANDED_OFF",
                ),
            ]
        )
        session.commit()
        assert completion._count_eligible(session, user.id, first.id, current.id, 7) == 1
    finally:
        session.close()


def test_completion_repository_exposes_frozen_identity_lookup() -> None:
    from app.validation.repository import ValidationRepository

    assert hasattr(ValidationRepository, "find_completion")


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
async def test_completion_concurrent_sessions_return_one_authoritative_row(
    monkeypatch, tmp_path
) -> None:
    import app.activities.completion as completion
    from app.validation.repository import ValidationRepository

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
    original_find = ValidationRepository.find_completion
    calls = 0
    lock = threading.Lock()

    def synchronize_find(self, **kwargs):
        nonlocal calls
        with lock:
            calls += 1
            should_wait = calls <= 2
        if should_wait:
            barrier.wait(timeout=10)
        return original_find(self, **kwargs)

    monkeypatch.setattr(ValidationRepository, "find_completion", synchronize_find)
    results = await asyncio.gather(
        *[
            asyncio.to_thread(
                lambda: asyncio.run(
                    completion.resolve_completion(completion.ResolveCompletionInput(**values))
                )
            )
            for _ in range(2)
        ]
    )

    assert {result.completion_id for result in results} == {results[0].completion_id}
    session = factory()
    try:
        assert session.query(CompletionDecision).count() == 1
        assert (
            session.query(Run).count() == 1
        )  # the loser recovery did not poison independent sessions
    finally:
        session.close()
