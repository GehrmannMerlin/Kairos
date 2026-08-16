"""M-07 task lifecycle activity tests (SQLite)."""

from __future__ import annotations

import asyncio
import threading

import app.activities.task_execution as task_execution
import pytest
from app.activities.task_execution import (
    CommitCheckpointInput,
    FailRunInput,
    commit_checkpoint,
    fail_run,
)
from app.auth.models import User
from app.domain.errors import DomainError
from app.domain.models import Checkpoint, DomainEvent, ResourceLease, Run, Task
from app.domain.repository import RunRepository, TaskRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.orm import sessionmaker


@pytest.mark.asyncio
async def test_fail_run_marks_task_and_run_failed(monkeypatch, tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'activities.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)

    session = factory()
    try:
        user = User(email="fail@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="fail me", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=0
        )
        # 直接置为 RUNNING（不经状态机，聚焦 fail 命令的收尾行为）
        task.state = "RUNNING"
        run.state = "running"
        session.commit()
        task_id = task.id
        run_id = run.id
        user_id = user.id
    finally:
        session.close()

    await fail_run(FailRunInput(task_id=task_id, user_id=user_id, run_id=run_id))

    session = factory()
    try:
        stored_task = session.get(Task, task_id)
        stored_run = session.get(Run, run_id)
        assert stored_task is not None
        assert stored_run is not None
        assert stored_task.state == "FAILED"
        assert stored_run.state == "failed"
        assert stored_run.finished_at is not None
    finally:
        session.close()


def _commit_input(*, user_id: int, task_id: int, batch: str, fp: str) -> CommitCheckpointInput:
    return CommitCheckpointInput(
        task_id=task_id,
        user_id=user_id,
        run_id=1,
        spec_version=1,
        plan_version=0,
        batch_identity=batch,
        node_run_id=None,
        input_fingerprint=fp,
        committed_refs={"n": 1, "artifact_id": 99, "raw_ref": 4},
        content_hash=None,
    )


@pytest.mark.asyncio
async def test_commit_checkpoint_reuses_same_batch_activity(monkeypatch, tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'activities.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)

    session = factory()
    try:
        user = User(email="cp@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="cp reuse", task_type=None)
        RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=0
        )
        task_id = task.id
        user_id = user.id
    finally:
        session.close()

    inp = _commit_input(user_id=user_id, task_id=task_id, batch="unit-1", fp="fp-1")
    first = await commit_checkpoint(inp)
    second = await commit_checkpoint(inp)

    assert first.reused is False
    assert second.checkpoint_id == first.checkpoint_id
    assert second.reused is True

    session = factory()
    try:
        rows = session.query(Checkpoint).filter_by(run_id=1).all()
        assert len(rows) == 1
        assert [
            event.event_type for event in session.query(DomainEvent).order_by(DomainEvent.id)
        ] == ["run.checkpoint_committed"]
        assert "artifact_id" not in session.query(DomainEvent).one().payload["counts"]
    finally:
        session.close()


@pytest.mark.asyncio
async def test_commit_checkpoint_same_batch_different_fingerprint_raises(
    monkeypatch, tmp_path
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'activities.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)

    session = factory()
    try:
        user = User(email="cp-conflict@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="cp conflict", task_type=None)
        RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=0
        )
        task_id = task.id
        user_id = user.id
    finally:
        session.close()

    await commit_checkpoint(
        _commit_input(user_id=user_id, task_id=task_id, batch="unit-1", fp="fp-1")
    )
    with pytest.raises(DomainError):
        await commit_checkpoint(
            _commit_input(user_id=user_id, task_id=task_id, batch="unit-1", fp="fp-DIFFERENT")
        )


@pytest.mark.asyncio
async def test_concurrent_checkpoint_activity_reports_creator_and_reuser(
    monkeypatch, tmp_path
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'checkpoint-concurrent-result.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user = User(email="checkpoint-race@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="checkpoint", task_type=None)
        RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=0
        )
        inp = _commit_input(user_id=user.id, task_id=task.id, batch="unit-1", fp="fp-1")
    finally:
        session.close()

    barrier = threading.Barrier(2)

    def synchronize_checkpoint_insert(conn, _cursor, statement, *_args) -> None:
        if statement.lstrip().upper().startswith("INSERT INTO CHECKPOINTS"):
            barrier.wait(timeout=10)

    sqlalchemy_event.listen(engine, "before_cursor_execute", synchronize_checkpoint_insert)
    try:
        results = await asyncio.gather(commit_checkpoint(inp), commit_checkpoint(inp))
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", synchronize_checkpoint_insert)

    assert sorted(result.reused for result in results) == [False, True]
    session = factory()
    try:
        assert session.query(Checkpoint).count() == 1
        assert (
            session.query(DomainEvent).filter_by(event_type="run.checkpoint_committed").count() == 1
        )
        assert session.query(Run).count() == 1  # losing savepoint did not poison the outer session
    finally:
        session.close()


def _spec_with_seeds(seed_urls: list[str]) -> dict:
    return {
        "schema_version": "m06.1",
        "task_type": "SPECIFIED_SOURCE",
        "goal": "seed ingest",
        "fields": [{"name": "标题", "type": "text", "required": True}],
        "auto_expand_fields": False,
        "source_scope": {"mode": "SPECIFIED_SOURCE", "seed_urls": seed_urls, "source_hints": []},
        "completion_conditions": [],
        "advanced_settings": {},
    }


@pytest.mark.asyncio
async def test_ensure_run_started_ingests_spec_seeds_into_frontier(monkeypatch, tmp_path) -> None:
    """DEPLOY-GATE-3 暴露的缺口：spec seed_urls 必须摄入 URL Frontier（DISCOVERED）。"""
    from app.activities.task_execution import EnsureRunStartedInput, ensure_run_started
    from app.discovery.models import FrontierState
    from app.domain.models import URLResource
    from app.domain.repository import SpecVersionRepository

    engine = create_engine(
        f"sqlite:///{tmp_path / 'seeds.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)

    session = factory()
    try:
        from datetime import UTC, datetime

        user = User(email="seed@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(
            user_id=user.id, title="seed task", task_type="SPECIFIED_SOURCE"
        )
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        spec = SpecVersionRepository(session).create(
            user_id=user.id,
            task_id=task.id,
            version=1,
            spec_type="collection",
            schema_version="m06.1",
            payload=_spec_with_seeds(["https://example.com"]),
        )
        spec.confirmed_at = datetime.now(UTC)
        spec.confirmed_by = user.id
        session.add(spec)
        task.state = "QUEUED"  # spec confirm 后任务进入 QUEUED（真实流经 confirm_spec）
        session.add(task)
        session.commit()
        task_id, run_id, user_id = task.id, run.id, user.id
    finally:
        session.close()

    await ensure_run_started(
        EnsureRunStartedInput(
            task_id=task_id, user_id=user_id, run_id=run_id, spec_version=1, plan_version=1
        )
    )
    replay = await ensure_run_started(
        EnsureRunStartedInput(
            task_id=task_id, user_id=user_id, run_id=run_id, spec_version=1, plan_version=1
        )
    )

    session = factory()
    try:
        urls = session.query(URLResource).filter_by(task_id=task_id).all()
        assert len(urls) == 1
        assert urls[0].url == "https://example.com"
        assert urls[0].status == FrontierState.DISCOVERED.value
        assert urls[0].source_type == "USER_SEED"
        stored_task = session.get(Task, task_id)
        assert stored_task is not None and stored_task.state == "RUNNING"
        assert [event.event_type for event in session.query(DomainEvent)].count("run.started") == 1
        assert replay.started is False
    finally:
        session.close()


@pytest.mark.asyncio
async def test_ensure_run_started_recovers_running_task_with_pending_run(
    monkeypatch, tmp_path
) -> None:
    """A prior partial start must be recoverable without duplicating run.started."""
    from app.activities.task_execution import EnsureRunStartedInput, ensure_run_started
    from app.domain.repository import SpecVersionRepository

    engine = create_engine(
        f"sqlite:///{tmp_path / 'recovery.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        from datetime import UTC, datetime

        user = User(email="recovery@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="recover", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        spec = SpecVersionRepository(session).create(
            user_id=user.id,
            task_id=task.id,
            version=1,
            spec_type="collection",
            schema_version="m06.1",
            payload=_spec_with_seeds([]),
        )
        spec.confirmed_at = datetime.now(UTC)
        task.state = "RUNNING"
        session.commit()
        ids = task.id, run.id, user.id
    finally:
        session.close()

    result = await ensure_run_started(
        EnsureRunStartedInput(
            task_id=ids[0], run_id=ids[1], user_id=ids[2], spec_version=1, plan_version=1
        )
    )

    session = factory()
    try:
        stored_run = session.get(Run, ids[1])
        assert stored_run is not None and stored_run.state == "running"
        assert result.started is True
        assert [event.event_type for event in session.query(DomainEvent)].count("run.started") == 1
    finally:
        session.close()


@pytest.mark.asyncio
async def test_ensure_run_started_rolls_back_task_claim_when_run_event_fails(
    monkeypatch, tmp_path
) -> None:
    from app.activities.task_execution import EnsureRunStartedInput, ensure_run_started
    from app.domain.repository import SpecVersionRepository

    engine = create_engine(
        f"sqlite:///{tmp_path / 'start-fault.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        from datetime import UTC, datetime

        user = User(email="start-fault@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="atomic start", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        spec = SpecVersionRepository(session).create(
            user_id=user.id,
            task_id=task.id,
            version=1,
            spec_type="collection",
            schema_version="m06.1",
            payload=_spec_with_seeds([]),
        )
        spec.confirmed_at = datetime.now(UTC)
        task.state = "QUEUED"
        session.commit()
        ids = task.id, run.id, user.id
    finally:
        session.close()

    monkeypatch.setattr(
        task_execution,
        "append_domain_event",
        lambda *_, **__: (_ for _ in ()).throw(RuntimeError("fault")),
    )
    with pytest.raises(RuntimeError, match="fault"):
        await ensure_run_started(
            EnsureRunStartedInput(
                task_id=ids[0], run_id=ids[1], user_id=ids[2], spec_version=1, plan_version=1
            )
        )

    session = factory()
    try:
        stored_task = session.get(Task, ids[0])
        stored_run = session.get(Run, ids[1])
        assert stored_task is not None and stored_task.state == "QUEUED"
        assert stored_run is not None and stored_run.state == "pending"
    finally:
        session.close()


@pytest.mark.asyncio
async def test_checkpoint_replay_backfills_missing_event(monkeypatch, tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'checkpoint-replay.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        user = User(email="checkpoint-replay@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="checkpoint", task_type=None)
        RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=0
        )
        session.add(
            Checkpoint(
                user_id=user.id,
                task_id=task.id,
                run_id=1,
                batch_identity="unit-1",
                spec_version=1,
                plan_version=0,
                node_run_id=None,
                input_fingerprint="fp-1",
                committed_object_refs={"fetched": 1},
                content_hash=None,
            )
        )
        session.commit()
        ids = user.id, task.id
    finally:
        session.close()

    result = await commit_checkpoint(
        _commit_input(user_id=ids[0], task_id=ids[1], batch="unit-1", fp="fp-1")
    )

    session = factory()
    try:
        assert result.reused is True
        assert [event.event_type for event in session.query(DomainEvent)] == [
            "run.checkpoint_committed"
        ]
    finally:
        session.close()


@pytest.mark.asyncio
async def test_ensure_run_started_concurrent_recovery_claims_run_started_once(
    monkeypatch, tmp_path
) -> None:
    """Two workers observing a pending Run must produce one start fact."""
    from app.activities.task_execution import EnsureRunStartedInput, ensure_run_started
    from app.domain.repository import SpecVersionRepository

    engine = create_engine(
        f"sqlite:///{tmp_path / 'start-concurrent.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(task_execution, "get_session_factory", lambda: factory)
    session = factory()
    try:
        from datetime import UTC, datetime

        user = User(email="start-concurrent@kairos.test", password_hash="hash")
        session.add(user)
        session.commit()
        task = TaskRepository(session).create(user_id=user.id, title="race", task_type=None)
        run = RunRepository(session).create(
            user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
        )
        spec = SpecVersionRepository(session).create(
            user_id=user.id,
            task_id=task.id,
            version=1,
            spec_type="collection",
            schema_version="m06.1",
            payload=_spec_with_seeds([]),
        )
        spec.confirmed_at = datetime.now(UTC)
        # Exercise the restartable RUNNING-task/pending-Run recovery path so
        # both workers reach the Run claim before any unrelated task write.
        task.state = "RUNNING"
        session.commit()
        inp = EnsureRunStartedInput(
            task_id=task.id, run_id=run.id, user_id=user.id, spec_version=1, plan_version=1
        )
    finally:
        session.close()

    barrier = threading.Barrier(2)

    def synchronize_run_claim(conn, _cursor, statement, *_args) -> None:
        if statement.lstrip().upper().startswith("UPDATE RUNS"):
            barrier.wait(timeout=10)

    sqlalchemy_event.listen(engine, "before_cursor_execute", synchronize_run_claim)
    try:
        results = await asyncio.gather(
            *[asyncio.to_thread(lambda: asyncio.run(ensure_run_started(inp))) for _ in range(2)]
        )
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", synchronize_run_claim)

    session = factory()
    try:
        assert sorted(result.started for result in results) == [False, True]
        assert session.get(Run, inp.run_id).state == "running"
        assert session.query(DomainEvent).filter_by(event_type="run.started").count() == 1
        active_leases = session.query(ResourceLease).filter_by(state="active").all()
        assert {(lease.scope, lease.scope_key, lease.holder_id) for lease in active_leases} == {
            ("global", "deploy", f"run{inp.run_id}"),
            ("user", str(inp.user_id), f"run{inp.run_id}"),
        }
    finally:
        session.close()
